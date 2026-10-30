//+------------------------------------------------------------------+
//|                                            ATR_EW_Executor.mq5   |
//|  Consumes ATR-EW signals from the Python bridge and executes     |
//|  them with full SL/TP, partial take-profits and risk controls.   |
//|                                                                  |
//|  Signal wire format (one CSV line, see bridge/app/dispatch.py):  |
//|   seq,signal_id,symbol,direction,entry,sl,tp1,tp2,tp3,recv_unix  |
//|                                                                  |
//|  Sources:                                                        |
//|   FILE : reads <Common\Files>\ATR_EW\inbox\*.sig (same machine)  |
//|          Files are never deleted by the EA, so several terminals |
//|          (copy-trading on one PC) can all consume the same feed. |
//|   HTTP : polls the bridge via WebRequest. MT5 only uses ports    |
//|          80/443 — expose the bridge there and whitelist the URL  |
//|          in Tools > Options > Expert Advisors.                   |
//|                                                                  |
//|  Multi-TP: one position, broker-side SL + TP3. The EA closes     |
//|  TP1%/TP2% partially when price reaches TP1/TP2, then moves SL   |
//|  to break-even / TP1. If the EA or terminal dies, the position   |
//|  is still protected by the broker-side SL and TP3.               |
//|                                                                  |
//|  Attach to ONE chart only (any symbol). It manages all symbols.  |
//+------------------------------------------------------------------+
#property copyright "ATR-EW signal stack"
#property version   "1.00"
#property description "Executes ATR + Elliott Wave hybrid signals with partial TPs and risk controls."

#include <Trade\Trade.mqh>

//--- enums
enum ENUM_SIGNAL_SOURCE { SOURCE_FILE = 0, SOURCE_HTTP = 1 };
enum ENUM_EXEC_MODE     { EXEC_AUTO = 0,   EXEC_ALERT_ONLY = 1 };
enum ENUM_LOT_MODE      { LOTS_FIXED = 0,  LOTS_RISK_PERCENT = 1 };

//--- inputs
input group "Signal source"
input ENUM_SIGNAL_SOURCE InpSource        = SOURCE_FILE;
input string  InpInboxFolder              = "ATR_EW\\inbox";                         // Common\Files subfolder
input string  InpBridgePollUrl            = "http://127.0.0.1/api/v1/signals/pending"; // HTTP mode (port 80/443 only)
input string  InpBridgeToken              = "";                                      // X-Bridge-Token
input int     InpPollSeconds              = 2;
input int     InpMaxSignalAgeSec          = 900;                                     // ignore signals older than this

input group "Execution"
input ENUM_EXEC_MODE InpExecMode          = EXEC_AUTO;      // ALERT_ONLY = human review, no orders
input ulong   InpMagic                    = 520520;
input string  InpSymbolMap                = "";             // canonical=broker; e.g. "XAUUSD=GOLD;EURUSD=EURUSD.m"
input string  InpSymbolSuffix             = "";             // appended when not in map, e.g. ".r"
input int     InpMaxSlippagePoints        = 30;
input double  InpMaxEntryDeviationR       = 0.30;           // skip if |price-entry| > 0.30 x signal risk
input double  InpMaxSpreadFracOfRisk      = 0.15;           // skip if spread > 15% of stop distance

input group "Position sizing"
input ENUM_LOT_MODE InpLotMode            = LOTS_RISK_PERCENT;
input double  InpFixedLots                = 0.01;
input double  InpRiskPercent              = 0.50;           // % of equity lost if SL is hit
input double  InpCopyMultiplier           = 1.0;            // copy-trading: scale this account vs. master
input double  InpMaxLots                  = 5.0;

input group "Multi-TP management"
input double  InpTP1ClosePct              = 40.0;           // % of initial volume closed at TP1
input double  InpTP2ClosePct              = 30.0;           // % of initial volume closed at TP2 (rest -> TP3)
input bool    InpMoveToBEAfterTP1         = true;
input int     InpBEOffsetPoints           = 0;              // lock a few points beyond entry
input bool    InpTrailToTP1AfterTP2       = true;

input group "Risk controls"
input int     InpMaxTradesPerDay          = 6;
input int     InpMaxOpenPositions         = 3;
input bool    InpOnePositionPerSymbol     = true;
input double  InpMaxDailyLossPct          = 2.0;            // pause new entries for the rest of the server day
input double  InpMaxDrawdownPct           = 8.0;            // from equity peak -> pause until manual reset
input bool    InpResetDrawdownPause       = false;          // set true once to clear pause + reset peak

input group "Reporting"
input bool    InpWriteJournal             = true;           // Common\Files\ATR_EW\journal_<login>.csv
input bool    InpReportToBridge           = false;
input string  InpReportUrl                = "http://127.0.0.1/api/v1/executions";
input bool    InpPushNotifications        = true;           // SendNotification to MT5 mobile app

//--- signal row
struct SignalRow
  {
   long     seq;
   string   id;
   string   symbol;
   string   direction;
   double   entry;
   double   sl;
   double   tp1;
   double   tp2;
   double   tp3;
   long     recvUnix;
  };

//--- globals
CTrade   g_trade;
string   g_processed[];
string   g_login;
datetime g_lastPoll    = 0;
datetime g_lastCleanup = 0;
bool     g_hedging     = true;

#define GV_POS_PREFIX "AEW_POS_"   // per-position keys; distinct from AEW_PEAK_/AEW_DDPAUSE_/AEW_LASTSEQ_

//+------------------------------------------------------------------+
//| Helpers: names, journal, notify                                  |
//+------------------------------------------------------------------+
string GvKey(long posId, string key)      { return GV_POS_PREFIX + IntegerToString(posId) + "_" + key; }
string GvPeak()                           { return "AEW_PEAK_" + g_login; }
string GvPause()                          { return "AEW_DDPAUSE_" + g_login; }
string GvLastSeq()                        { return "AEW_LASTSEQ_" + g_login; }
string ProcessedFile()                    { return "ATR_EW\\state\\processed_" + g_login + ".txt"; }
string JournalFile()                      { return "ATR_EW\\journal_" + g_login + ".csv"; }

string Clean(string s)
  {
   StringReplace(s, ",", ";");
   StringReplace(s, "\"", "'");
   StringReplace(s, "\r", " ");
   StringReplace(s, "\n", " ");
   return s;
  }

void Journal(string evt, long seq, string sym, string detail)
  {
   PrintFormat("[ATR-EW] %s seq=%I64d %s %s", evt, seq, sym, detail);
   if(!InpWriteJournal)
      return;
   int h = FileOpen(JournalFile(), FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return;
   if(FileSize(h) == 0)
      FileWriteString(h, "server_time,event,seq,symbol,detail\r\n");
   FileSeek(h, 0, SEEK_END);
   FileWriteString(h, TimeToString(TimeCurrent(), TIME_DATE | TIME_SECONDS) + "," + evt + "," +
                   IntegerToString(seq) + "," + sym + "," + Clean(detail) + "\r\n");
   FileClose(h);
  }

void Notify(string text)
  {
   Alert(text);
   if(InpPushNotifications && TerminalInfoInteger(TERMINAL_NOTIFICATIONS_ENABLED))
      SendNotification(text);
  }

void ReportToBridge(string evt, long seq, string sym, ulong ticket, double volume, double price,
                    double profit, double rMultiple, bool hasR, string detail)
  {
   if(!InpReportToBridge || MQLInfoInteger(MQL_TESTER))
      return;
   string json = "{\"account\":\"" + g_login + "\",\"seq\":" + IntegerToString(seq) +
                 ",\"event\":\"" + evt + "\",\"symbol\":\"" + sym + "\",\"ticket\":\"" + IntegerToString((long)ticket) + "\"" +
                 ",\"volume\":" + DoubleToString(volume, 2) + ",\"price\":" + DoubleToString(price, 6) +
                 ",\"profit\":" + DoubleToString(profit, 2) +
                 (hasR ? ",\"r_multiple\":" + DoubleToString(rMultiple, 3) : "") +
                 ",\"detail\":\"" + Clean(detail) + "\"}";
   char data[], result[];
   string resHeaders;
   int n = StringToCharArray(json, data, 0, WHOLE_ARRAY, CP_UTF8);
   if(n > 0)
      ArrayResize(data, n - 1);   // drop trailing \0
   string headers = "Content-Type: application/json\r\nX-Bridge-Token: " + InpBridgeToken + "\r\n";
   ResetLastError();
   int code = WebRequest("POST", InpReportUrl, headers, 3000, data, result, resHeaders);
   if(code != 200)
      PrintFormat("[ATR-EW] report failed code=%d err=%d (URL whitelisted? port 80/443?)", code, GetLastError());
  }

//+------------------------------------------------------------------+
//| Processed-signal registry (per account, persisted)               |
//+------------------------------------------------------------------+
bool IsProcessed(string id)
  {
   for(int i = ArraySize(g_processed) - 1; i >= 0; i--)
      if(g_processed[i] == id)
         return true;
   return false;
  }

void MarkProcessed(string id)
  {
   int n = ArraySize(g_processed);
   ArrayResize(g_processed, n + 1);
   g_processed[n] = id;
   int h = FileOpen(ProcessedFile(), FILE_READ | FILE_WRITE | FILE_TXT | FILE_ANSI | FILE_COMMON);
   if(h != INVALID_HANDLE)
     {
      FileSeek(h, 0, SEEK_END);
      FileWriteString(h, id + "\r\n");
      FileClose(h);
     }
  }

void LoadProcessed()
  {
   ArrayResize(g_processed, 0);
   int h = FileOpen(ProcessedFile(), FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
   if(h == INVALID_HANDLE)
      return;
   while(!FileIsEnding(h))
     {
      string s = FileReadString(h);
      StringTrimLeft(s);
      StringTrimRight(s);
      if(s == "")
         continue;
      int n = ArraySize(g_processed);
      ArrayResize(g_processed, n + 1);
      g_processed[n] = s;
     }
   FileClose(h);
   // keep memory bounded: only the last 2000 ids matter (older signals are stale anyway)
   int total = ArraySize(g_processed);
   if(total > 2000)
     {
      ArrayRemove(g_processed, 0, total - 2000);
     }
  }

//+------------------------------------------------------------------+
//| Symbol mapping + volume helpers                                  |
//+------------------------------------------------------------------+
string BrokerSymbol(string canonical)
  {
   if(InpSymbolMap != "")
     {
      string pairs[];
      int n = StringSplit(InpSymbolMap, ';', pairs);
      for(int i = 0; i < n; i++)
        {
         string kv[];
         if(StringSplit(pairs[i], '=', kv) == 2)
           {
            string k = kv[0], v = kv[1];
            StringTrimLeft(k); StringTrimRight(k);
            StringTrimLeft(v); StringTrimRight(v);
            if(k == canonical)
               return v;
           }
        }
     }
   return canonical + InpSymbolSuffix;
  }

int VolumeDigits(double step)
  {
   int d = 0;
   while(d < 8 && MathAbs(step * MathPow(10, d) - MathRound(step * MathPow(10, d))) > 1e-8)
      d++;
   return d;
  }

// Round DOWN to the volume step; return 0 if below the broker minimum (never round risk up).
double NormalizeVolumeDown(string sym, double vol)
  {
   double step = SymbolInfoDouble(sym, SYMBOL_VOLUME_STEP);
   double vmin = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);
   double vmax = SymbolInfoDouble(sym, SYMBOL_VOLUME_MAX);
   if(step <= 0)
      return 0;
   double v = MathFloor(vol / step + 1e-9) * step;
   v = MathMin(v, vmax);
   if(v < vmin - 1e-9)
      return 0;
   return NormalizeDouble(v, VolumeDigits(step));
  }

// Round a price to the symbol's tick size (some metals/indices reject digit-only rounding).
double RoundToTick(string sym, double price)
  {
   double tick = SymbolInfoDouble(sym, SYMBOL_TRADE_TICK_SIZE);
   int digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   if(tick > 0)
      price = MathRound(price / tick) * tick;
   return NormalizeDouble(price, digits);
  }

double CalcLots(string sym, bool isBuy, double price, double sl)
  {
   double vol = InpFixedLots;
   if(InpLotMode == LOTS_RISK_PERCENT)
     {
      double pnl = 0;
      if(!OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, 1.0, price, sl, pnl))
         return 0;
      double lossPerLot = MathAbs(pnl);
      if(lossPerLot <= 0)
         return 0;
      vol = AccountInfoDouble(ACCOUNT_EQUITY) * InpRiskPercent / 100.0 / lossPerLot;
     }
   vol *= InpCopyMultiplier;
   vol = MathMin(vol, InpMaxLots);
   return NormalizeVolumeDown(sym, vol);
  }

//+------------------------------------------------------------------+
//| Risk state                                                       |
//+------------------------------------------------------------------+
datetime ServerDayStart()
  {
   datetime t = TimeCurrent();
   return t - (t % 86400);
  }

// Trades opened today + realised PnL today, for this magic.
void TodayStats(int &tradesToday, double &realisedToday)
  {
   tradesToday = 0;
   realisedToday = 0;
   if(!HistorySelect(ServerDayStart(), TimeCurrent() + 60))
      return;
   int n = HistoryDealsTotal();
   for(int i = 0; i < n; i++)
     {
      ulong d = HistoryDealGetTicket(i);
      if(d == 0 || (ulong)HistoryDealGetInteger(d, DEAL_MAGIC) != InpMagic)
         continue;
      long entry = HistoryDealGetInteger(d, DEAL_ENTRY);
      if(entry == DEAL_ENTRY_IN)
         tradesToday++;
      realisedToday += HistoryDealGetDouble(d, DEAL_PROFIT) + HistoryDealGetDouble(d, DEAL_SWAP) +
                       HistoryDealGetDouble(d, DEAL_COMMISSION) + HistoryDealGetDouble(d, DEAL_FEE);
     }
  }

int OpenPositions(string symFilter, double &floating)
  {
   int cnt = 0;
   floating = 0;
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      floating += PositionGetDouble(POSITION_PROFIT) + PositionGetDouble(POSITION_SWAP);
      if(symFilter == "" || PositionGetString(POSITION_SYMBOL) == symFilter)
         cnt++;
     }
   return cnt;
  }

bool DrawdownPaused() { return GlobalVariableCheck(GvPause()) && GlobalVariableGet(GvPause()) > 0.5; }

void UpdateEquityPeak()
  {
   double eq = AccountInfoDouble(ACCOUNT_EQUITY);
   if(!GlobalVariableCheck(GvPeak()) || eq > GlobalVariableGet(GvPeak()))
      GlobalVariableSet(GvPeak(), eq);
   double peak = GlobalVariableGet(GvPeak());
   if(peak <= 0 || DrawdownPaused())
      return;
   double dd = (peak - eq) / peak * 100.0;
   if(dd >= InpMaxDrawdownPct)
     {
      GlobalVariableSet(GvPause(), 1.0);
      GlobalVariablesFlush();
      Journal("DD_PAUSE", 0, "", StringFormat("drawdown %.2f%% from peak %.2f — new entries paused", dd, peak));
      Notify(StringFormat("ATR-EW: drawdown %.2f%% hit limit %.2f%%. New entries PAUSED. Review, then reset via input.", dd, InpMaxDrawdownPct));
     }
  }

// Returns "" if allowed, otherwise the reason.
string RiskGate(string brokerSym)
  {
   if(DrawdownPaused())
      return "drawdown pause active";
   int tradesToday;
   double realised;
   TodayStats(tradesToday, realised);
   if(tradesToday >= InpMaxTradesPerDay)
      return StringFormat("max trades/day reached (%d)", tradesToday);
   double floating;
   int openAll = OpenPositions("", floating);
   double dayStartBalance = AccountInfoDouble(ACCOUNT_BALANCE) - realised;
   if(dayStartBalance > 0 && (realised + floating) <= -InpMaxDailyLossPct / 100.0 * dayStartBalance)
      return StringFormat("daily loss limit (%.2f)", realised + floating);
   if(openAll >= InpMaxOpenPositions)
      return StringFormat("max open positions (%d)", openAll);
   double f2;
   if(InpOnePositionPerSymbol && OpenPositions(brokerSym, f2) > 0)
      return "position already open on " + brokerSym;
   // Netting: ANY position on the symbol (manual or other EA) would merge with ours and lose its SL/TP.
   if(!g_hedging && PositionSelect(brokerSym))
      return "netting account: a position already exists on " + brokerSym;
   if(!TerminalInfoInteger(TERMINAL_TRADE_ALLOWED) || !MQLInfoInteger(MQL_TRADE_ALLOWED))
      return "algo trading disabled in terminal";
   return "";
  }

//+------------------------------------------------------------------+
//| Parsing                                                          |
//+------------------------------------------------------------------+
bool ParseRow(string line, SignalRow &s)
  {
   StringTrimLeft(line);
   StringTrimRight(line);
   if(line == "" || StringFind(line, "seq,") == 0 || StringFind(line, "#") == 0)
      return false;
   string f[];
   if(StringSplit(line, ',', f) != 10)
      return false;
   s.seq       = StringToInteger(f[0]);
   s.id        = f[1];
   s.symbol    = f[2];
   s.direction = f[3];
   s.entry     = StringToDouble(f[4]);
   s.sl        = StringToDouble(f[5]);
   s.tp1       = StringToDouble(f[6]);
   s.tp2       = StringToDouble(f[7]);
   s.tp3       = StringToDouble(f[8]);
   s.recvUnix  = StringToInteger(f[9]);
   if(s.seq <= 0 || s.id == "" || (s.direction != "BUY" && s.direction != "SELL"))
      return false;
   if(s.entry <= 0 || s.sl <= 0 || s.tp1 <= 0 || s.tp2 <= 0 || s.tp3 <= 0)
      return false;
   if(s.direction == "BUY" && !(s.sl < s.entry && s.entry < s.tp1 && s.tp1 <= s.tp2 && s.tp2 <= s.tp3))
      return false;
   if(s.direction == "SELL" && !(s.sl > s.entry && s.entry > s.tp1 && s.tp1 >= s.tp2 && s.tp2 >= s.tp3))
      return false;
   return true;
  }

//+------------------------------------------------------------------+
//| Execution                                                        |
//+------------------------------------------------------------------+
long FindPositionId(string sym, string comment, ulong orderTicket)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic || PositionGetString(POSITION_SYMBOL) != sym)
         continue;
      long id = PositionGetInteger(POSITION_IDENTIFIER);
      if((ulong)id == orderTicket || PositionGetString(POSITION_COMMENT) == comment)
         return id;
     }
   return -1;
  }

bool SelectPositionById(long posId)
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong t = PositionGetTicket(i);
      if(t != 0 && PositionGetInteger(POSITION_IDENTIFIER) == posId)
         return true;
     }
   return false;
  }

// Position id of the order just sent. Prefer the fill deal's DEAL_POSITION_ID; fall back to a
// scan, retried briefly because the position list can lag the synchronous OrderSend result.
long ResolvePositionId(string sym, string comment)
  {
   ulong deal  = g_trade.ResultDeal();
   ulong order = g_trade.ResultOrder();
   for(int attempt = 0; attempt < 10; attempt++)
     {
      if(deal > 0 && HistoryDealSelect(deal))
        {
         long pid = HistoryDealGetInteger(deal, DEAL_POSITION_ID);
         if(pid > 0 && SelectPositionById(pid))
            return pid;
        }
      long pid2 = FindPositionId(sym, comment, order);
      if(pid2 >= 0)
         return pid2;
      Sleep(100);
     }
   return -1;
  }

bool SendMarket(bool isBuy, double lots, string sym, double sl, double tp, string comment)
  {
   for(int attempt = 0; attempt < 3; attempt++)
     {
      bool ok = isBuy ? g_trade.Buy(lots, sym, 0.0, sl, tp, comment)
                      : g_trade.Sell(lots, sym, 0.0, sl, tp, comment);
      uint rc = g_trade.ResultRetcode();
      if(ok && (rc == TRADE_RETCODE_DONE || rc == TRADE_RETCODE_DONE_PARTIAL || rc == TRADE_RETCODE_PLACED))
         return true;
      if(rc != TRADE_RETCODE_REQUOTE && rc != TRADE_RETCODE_PRICE_CHANGED && rc != TRADE_RETCODE_PRICE_OFF)
         return false;
      Sleep(300);
     }
   return false;
  }

void ExecuteSignal(SignalRow &s)
  {
   if(IsProcessed(s.id))
      return;
   MarkProcessed(s.id);   // mark FIRST: a crash mid-execution must never cause a duplicate order

   long age = (long)TimeGMT() - s.recvUnix;
   if(age > InpMaxSignalAgeSec)
     {
      Journal("SKIP", s.seq, s.symbol, StringFormat("stale: %I64d s old", age));
      return;
     }

   string sym = BrokerSymbol(s.symbol);
   if(!SymbolSelect(sym, true) || SymbolInfoInteger(sym, SYMBOL_TRADE_MODE) == SYMBOL_TRADE_MODE_DISABLED)
     {
      Journal("SKIP", s.seq, sym, "symbol not available / trading disabled (check InpSymbolMap)");
      return;
     }
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
   double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
   bool   isBuy  = (s.direction == "BUY");
   string levels = StringFormat("%s %s entry=%s sl=%s tp1=%s tp2=%s tp3=%s", s.direction, sym,
                                DoubleToString(s.entry, digits), DoubleToString(s.sl, digits),
                                DoubleToString(s.tp1, digits), DoubleToString(s.tp2, digits), DoubleToString(s.tp3, digits));

   if(InpExecMode == EXEC_ALERT_ONLY)
     {
      Journal("ALERT", s.seq, sym, levels);
      Notify("ATR-EW signal (manual review): " + levels);
      return;
     }

   string gate = RiskGate(sym);
   if(gate != "")
     {
      Journal("SKIP", s.seq, sym, gate);
      ReportToBridge("SKIP", s.seq, sym, 0, 0, 0, 0, 0, false, gate);
      return;
     }

   MqlTick tick;
   if(!SymbolInfoTick(sym, tick) || tick.bid <= 0 || tick.ask <= 0)
     {
      Journal("SKIP", s.seq, sym, "no tick");
      return;
     }
   double price    = isBuy ? tick.ask : tick.bid;
   double riskDist = MathAbs(s.entry - s.sl);
   string why = "";
   if(MathAbs(price - s.entry) > InpMaxEntryDeviationR * riskDist)
      why = StringFormat("price %s too far from signal entry (>%.2fR)", DoubleToString(price, digits), InpMaxEntryDeviationR);
   else if(isBuy ? (price <= s.sl || price >= s.tp1) : (price >= s.sl || price <= s.tp1))
      why = "price already beyond SL or TP1";
   else if((tick.ask - tick.bid) > InpMaxSpreadFracOfRisk * riskDist)
      why = StringFormat("spread %s too wide vs stop", DoubleToString(tick.ask - tick.bid, digits));
   else
     {
      // Stops are checked against the price they trigger on: bid for buys, ask for sells.
      double minDist = (double)SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL) * point;
      double ref = isBuy ? tick.bid : tick.ask;
      if(MathAbs(ref - s.sl) < minDist || MathAbs(s.tp3 - ref) < minDist)
         why = "SL/TP inside broker stops level";
     }
   if(why != "")
     {
      Journal("SKIP", s.seq, sym, why);
      ReportToBridge("SKIP", s.seq, sym, 0, 0, 0, 0, 0, false, why);
      return;
     }

   double sl  = RoundToTick(sym, s.sl);
   double tp3 = RoundToTick(sym, s.tp3);
   double lots = CalcLots(sym, isBuy, price, sl);
   if(lots <= 0)
     {
      Journal("SKIP", s.seq, sym, "computed lot below broker minimum (risk% too small for this stop)");
      return;
     }
   double margin = 0;
   if(OrderCalcMargin(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, lots, price, margin) &&
      margin > AccountInfoDouble(ACCOUNT_MARGIN_FREE))
     {
      Journal("SKIP", s.seq, sym, "insufficient free margin");
      return;
     }

   g_trade.SetTypeFillingBySymbol(sym);
   string comment = "AEW#" + IntegerToString(s.seq);
   bool ok = SendMarket(isBuy, lots, sym, sl, tp3, comment);

   // Some brokers reject stops on market orders: open naked, protect immediately, close if protection fails.
   if(!ok && g_trade.ResultRetcode() == TRADE_RETCODE_INVALID_STOPS)
     {
      ok = SendMarket(isBuy, lots, sym, 0.0, 0.0, comment);
      if(ok)
        {
         long pid = ResolvePositionId(sym, comment);
         if(pid < 0)
           {
            // Cannot find it to protect or close it: do not pretend otherwise.
            Journal("ERROR", s.seq, sym, "UNPROTECTED POSITION: opened without SL/TP and not found — act manually");
            Notify("ATR-EW URGENT: " + sym + " position opened WITHOUT SL/TP and could not be located. Check the terminal NOW.");
            return;
           }
         if(!SelectPositionById(pid) || !g_trade.PositionModify((ulong)PositionGetInteger(POSITION_TICKET), sl, tp3))
           {
            bool closed = SelectPositionById(pid) && g_trade.PositionClose((ulong)PositionGetInteger(POSITION_TICKET));
            string msg = closed ? "could not attach SL/TP — position closed for safety"
                                : "UNPROTECTED POSITION: could not attach SL/TP AND close failed — act manually";
            Journal("ERROR", s.seq, sym, msg);
            Notify("ATR-EW: " + sym + " " + msg);
            return;
           }
        }
     }
   if(!ok)
     {
      string err = StringFormat("order failed rc=%u %s", g_trade.ResultRetcode(), g_trade.ResultRetcodeDescription());
      Journal("ERROR", s.seq, sym, err);
      ReportToBridge("ERROR", s.seq, sym, 0, lots, price, 0, 0, false, err);
      return;
     }

   long posId = ResolvePositionId(sym, comment);
   if(posId < 0 || !SelectPositionById(posId))
     {
      // Broker-side SL + TP3 still protect it; only partial-TP management is missing.
      Journal("ERROR", s.seq, sym, "order filled but position not found — SL/TP3 set, partial TPs NOT managed");
      Notify("ATR-EW: " + sym + " filled but not tracked — partial TPs will not run. Check terminal.");
      return;
     }
   double openPrice = PositionGetDouble(POSITION_PRICE_OPEN);
   double vol       = PositionGetDouble(POSITION_VOLUME);
   double pnl = 0, riskMoney = 0;
   if(OrderCalcProfit(isBuy ? ORDER_TYPE_BUY : ORDER_TYPE_SELL, sym, vol, openPrice, sl, pnl))
      riskMoney = MathAbs(pnl);

   GlobalVariableSet(GvKey(posId, "seq"),   (double)s.seq);
   GlobalVariableSet(GvKey(posId, "init"),  vol);
   GlobalVariableSet(GvKey(posId, "entry"), openPrice);
   GlobalVariableSet(GvKey(posId, "tp1"),   s.tp1);
   GlobalVariableSet(GvKey(posId, "tp2"),   s.tp2);
   GlobalVariableSet(GvKey(posId, "risk"),  riskMoney);
   GlobalVariableSet(GvKey(posId, "stage"), 0);
   GlobalVariablesFlush();

   string detail = StringFormat("%s lots=%.2f open=%s slip=%.1fpts risk=%.2f %s", levels, vol,
                                DoubleToString(openPrice, digits), MathAbs(openPrice - s.entry) / point, riskMoney,
                                AccountInfoString(ACCOUNT_CURRENCY));
   Journal("OPEN", s.seq, sym, detail);
   ReportToBridge("OPEN", s.seq, sym, (ulong)posId, vol, openPrice, 0, 0, false, detail);
  }

//+------------------------------------------------------------------+
//| Position management: partial TPs, BE, trail-to-TP1               |
//+------------------------------------------------------------------+
void ManagePositions()
  {
   for(int i = PositionsTotal() - 1; i >= 0; i--)
     {
      ulong ticket = PositionGetTicket(i);
      if(ticket == 0 || (ulong)PositionGetInteger(POSITION_MAGIC) != InpMagic)
         continue;
      long posId = PositionGetInteger(POSITION_IDENTIFIER);
      if(!GlobalVariableCheck(GvKey(posId, "init")))
         continue;   // not opened by this EA instance's bookkeeping — leave it alone

      string sym    = PositionGetString(POSITION_SYMBOL);
      bool   isBuy  = (PositionGetInteger(POSITION_TYPE) == POSITION_TYPE_BUY);
      double vol    = PositionGetDouble(POSITION_VOLUME);
      double curSL  = PositionGetDouble(POSITION_SL);
      double curTP  = PositionGetDouble(POSITION_TP);
      double init   = GlobalVariableGet(GvKey(posId, "init"));
      double entry  = GlobalVariableGet(GvKey(posId, "entry"));
      double tp1    = GlobalVariableGet(GvKey(posId, "tp1"));
      double tp2    = GlobalVariableGet(GvKey(posId, "tp2"));
      int    stage  = (int)GlobalVariableGet(GvKey(posId, "stage"));
      long   seq    = (long)GlobalVariableGet(GvKey(posId, "seq"));
      int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);
      double point  = SymbolInfoDouble(sym, SYMBOL_POINT);
      double vmin   = SymbolInfoDouble(sym, SYMBOL_VOLUME_MIN);

      MqlTick tick;
      if(!SymbolInfoTick(sym, tick))
         continue;
      double px = isBuy ? tick.bid : tick.ask;   // price at which we could close

      // --- partial closes (one step per timer tick; position re-read next tick)
      if(stage < 2)
        {
         double target = (stage == 0) ? tp1 : tp2;
         bool   hit    = isBuy ? (px >= target) : (px <= target);
         if(hit)
           {
            double pct = (stage == 0) ? InpTP1ClosePct : InpTP2ClosePct;
            double closeVol = NormalizeVolumeDown(sym, init * pct / 100.0);
            string tag = (stage == 0) ? "TP1" : "TP2";
            if(closeVol > 0 && vol - closeVol >= vmin - 1e-9)
              {
               // Ticket overload is for hedging accounts; netting accounts close by symbol.
               bool closedOk = g_hedging ? g_trade.PositionClosePartial(ticket, closeVol, InpMaxSlippagePoints)
                                         : g_trade.PositionClosePartial(sym, closeVol, InpMaxSlippagePoints);
               if(!closedOk)
                 {
                  Journal("ERROR", seq, sym, tag + " partial close failed: " + g_trade.ResultRetcodeDescription());
                  continue;   // retry next tick
                 }
               Journal("TP_HIT", seq, sym, StringFormat("%s partial %.2f lots @ %s", tag, closeVol, DoubleToString(px, digits)));
              }
            else
               Journal("TP_HIT", seq, sym, tag + " reached; volume too small to split — managing stop only");
            GlobalVariableSet(GvKey(posId, "stage"), stage + 1);
            GlobalVariablesFlush();
            continue;
           }
        }

      // --- desired stop for the current stage (idempotent: retried until it sticks)
      double want = curSL;
      if(stage >= 1 && InpMoveToBEAfterTP1)
        {
         double be = entry + (isBuy ? 1 : -1) * InpBEOffsetPoints * point;
         if(isBuy ? (curSL == 0 || be > want) : (curSL == 0 || be < want))
            want = be;
        }
      if(stage >= 2 && InpTrailToTP1AfterTP2)
        {
         if(isBuy ? (tp1 > want) : (want == 0 || tp1 < want))
            want = tp1;
        }
      want = RoundToTick(sym, want);
      if(MathAbs(want - curSL) > point / 2.0)
        {
         double stopsLvl = (double)MathMax(SymbolInfoInteger(sym, SYMBOL_TRADE_STOPS_LEVEL),
                                           SymbolInfoInteger(sym, SYMBOL_TRADE_FREEZE_LEVEL)) * point;
         bool valid = isBuy ? (want < tick.bid - stopsLvl) : (want > tick.ask + stopsLvl);
         if(valid)
           {
            if(g_trade.PositionModify(ticket, want, curTP))
               Journal("SL_MOVE", seq, sym, StringFormat("stage %d SL %s -> %s", stage,
                       DoubleToString(curSL, digits), DoubleToString(want, digits)));
           }
        }
     }
  }

//+------------------------------------------------------------------+
//| Sources                                                          |
//+------------------------------------------------------------------+
// ArraySort() is numeric-only in MQL5; small insertion sort for file names.
void SortStrings(string &a[])
  {
   int n = ArraySize(a);
   for(int i = 1; i < n; i++)
     {
      string key = a[i];
      int j = i - 1;
      while(j >= 0 && StringCompare(a[j], key) > 0)
        {
         a[j + 1] = a[j];
         j--;
        }
      a[j + 1] = key;
     }
  }

void PollFiles()
  {
   string fname;
   long h = FileFindFirst(InpInboxFolder + "\\*.sig", fname, FILE_COMMON);
   if(h == INVALID_HANDLE)
      return;
   string names[];
   do
     {
      int n = ArraySize(names);
      ArrayResize(names, n + 1);
      names[n] = fname;
     }
   while(FileFindNext(h, fname));
   FileFindClose(h);
   SortStrings(names);   // zero-padded seq prefix => chronological

   for(int i = 0; i < ArraySize(names); i++)
     {
      // "<seq>_<signal_id>.sig" -> skip already-processed ids without opening the file
      int us = StringFind(names[i], "_");
      string id = (us > 0) ? StringSubstr(names[i], us + 1, StringLen(names[i]) - us - 1 - 4) : "";
      if(id != "" && IsProcessed(id))
         continue;
      int fh = FileOpen(InpInboxFolder + "\\" + names[i], FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ);
      if(fh == INVALID_HANDLE)
         continue;
      string line = FileReadString(fh);
      FileClose(fh);
      SignalRow s;
      if(ParseRow(line, s))
         ExecuteSignal(s);
      else if(id != "")
        {
         MarkProcessed(id);
         Journal("ERROR", 0, "", "unparseable signal file " + names[i]);
        }
     }
  }

void PollHttp()
  {
   long lastSeq = GlobalVariableCheck(GvLastSeq()) ? (long)GlobalVariableGet(GvLastSeq()) : 0;
   string url = InpBridgePollUrl + "?after=" + IntegerToString(lastSeq);
   string headers = "X-Bridge-Token: " + InpBridgeToken + "\r\n";
   char data[], result[];
   string resHeaders;
   ResetLastError();
   int code = WebRequest("GET", url, headers, 3000, data, result, resHeaders);
   if(code != 200)
     {
      static datetime lastWarn = 0;
      if(TimeLocal() - lastWarn > 300)
        {
         PrintFormat("[ATR-EW] poll failed code=%d err=%d. Whitelist URL in Tools>Options>Expert Advisors; bridge must be on port 80/443.",
                     code, GetLastError());
         lastWarn = TimeLocal();
        }
      return;
     }
   string body = CharArrayToString(result, 0, WHOLE_ARRAY, CP_UTF8);
   string lines[];
   int n = StringSplit(body, '\n', lines);
   for(int i = 0; i < n; i++)
     {
      // "# latest_seq=N": if the bridge DB was reset, our cursor is ahead of it -> resync.
      if(StringFind(lines[i], "# latest_seq=") == 0)
        {
         long latest = StringToInteger(StringSubstr(lines[i], 13));
         if(latest < lastSeq)
           {
            Journal("RESYNC", 0, "", StringFormat("bridge latest_seq %I64d < cursor %I64d — resetting cursor", latest, lastSeq));
            lastSeq = latest;
           }
         continue;
        }
      SignalRow s;
      if(!ParseRow(lines[i], s))
         continue;
      ExecuteSignal(s);
      if(s.seq > lastSeq)
         lastSeq = s.seq;
     }
   GlobalVariableSet(GvLastSeq(), (double)lastSeq);
  }

//+------------------------------------------------------------------+
//| Housekeeping: drop bookkeeping of positions closed > 1h ago      |
//+------------------------------------------------------------------+
void CleanupGlobals()
  {
   for(int i = GlobalVariablesTotal() - 1; i >= 0; i--)
     {
      string name = GlobalVariableName(i);
      if(StringFind(name, GV_POS_PREFIX) != 0)
         continue;   // never touches AEW_PEAK_ / AEW_DDPAUSE_ / AEW_LASTSEQ_
      int start = StringLen(GV_POS_PREFIX);
      int us = StringFind(name, "_", start);
      if(us < 0)
         continue;
      long posId = StringToInteger(StringSubstr(name, start, us - start));
      // GlobalVariableTime is terminal-local time, so compare with TimeLocal().
      if(posId > 0 && !SelectPositionById(posId) && TimeLocal() - GlobalVariableTime(name) > 3600)
         GlobalVariableDel(name);
     }
  }

//+------------------------------------------------------------------+
//| Close reporting -> journal + bridge (this is the measurement)    |
//+------------------------------------------------------------------+
void OnTradeTransaction(const MqlTradeTransaction &trans, const MqlTradeRequest &request, const MqlTradeResult &result)
  {
   if(trans.type != TRADE_TRANSACTION_DEAL_ADD || trans.deal == 0)
      return;
   if(!HistoryDealSelect(trans.deal))
      return;
   if((ulong)HistoryDealGetInteger(trans.deal, DEAL_MAGIC) != InpMagic)
      return;
   long entry = HistoryDealGetInteger(trans.deal, DEAL_ENTRY);
   if(entry != DEAL_ENTRY_OUT && entry != DEAL_ENTRY_OUT_BY && entry != DEAL_ENTRY_INOUT)
      return;

   long   posId  = HistoryDealGetInteger(trans.deal, DEAL_POSITION_ID);
   string sym    = HistoryDealGetString(trans.deal, DEAL_SYMBOL);
   double vol    = HistoryDealGetDouble(trans.deal, DEAL_VOLUME);
   double price  = HistoryDealGetDouble(trans.deal, DEAL_PRICE);
   double profit = HistoryDealGetDouble(trans.deal, DEAL_PROFIT) + HistoryDealGetDouble(trans.deal, DEAL_SWAP) +
                   HistoryDealGetDouble(trans.deal, DEAL_COMMISSION) + HistoryDealGetDouble(trans.deal, DEAL_FEE);
   long   reason = HistoryDealGetInteger(trans.deal, DEAL_REASON);
   long   seq    = GlobalVariableCheck(GvKey(posId, "seq")) ? (long)GlobalVariableGet(GvKey(posId, "seq")) : 0;
   double risk   = GlobalVariableCheck(GvKey(posId, "risk")) ? GlobalVariableGet(GvKey(posId, "risk")) : 0;
   bool   hasR   = risk > 0;
   double r      = hasR ? profit / risk : 0;   // this slice's contribution; sum over slices = trade R
   int    digits = (int)SymbolInfoInteger(sym, SYMBOL_DIGITS);

   // CLOSE vs PARTIAL from deal history (the position list can lag DEAL_ADD).
   double volIn = 0, volOut = 0;
   if(HistorySelectByPosition(posId))
     {
      for(int i = HistoryDealsTotal() - 1; i >= 0; i--)
        {
         ulong d = HistoryDealGetTicket(i);
         long  e = HistoryDealGetInteger(d, DEAL_ENTRY);
         double v = HistoryDealGetDouble(d, DEAL_VOLUME);
         if(e == DEAL_ENTRY_IN)
            volIn += v;
         else
            volOut += v;
        }
     }
   string evt    = (volIn > 0 && volOut >= volIn - 1e-8) ? "CLOSE" : "PARTIAL";
   string why    = (reason == DEAL_REASON_SL) ? "SL" : (reason == DEAL_REASON_TP) ? "TP" :
                   (reason == DEAL_REASON_SO) ? "STOP_OUT" : "EA/manual";
   string detail = StringFormat("%s vol=%.2f price=%s profit=%.2f R_contrib=%.2f", why, vol,
                                DoubleToString(price, digits), profit, r);
   Journal(evt, seq, sym, detail);
   ReportToBridge(evt, seq, sym, (ulong)posId, vol, price, profit, r, hasR, detail);
  }

//+------------------------------------------------------------------+
//| Lifecycle                                                        |
//+------------------------------------------------------------------+
int OnInit()
  {
   g_login = IntegerToString(AccountInfoInteger(ACCOUNT_LOGIN));
   if(InpTP1ClosePct < 0 || InpTP2ClosePct < 0 || InpTP1ClosePct + InpTP2ClosePct >= 100.0)
     {
      Print("[ATR-EW] TP1% + TP2% must be < 100 (the remainder runs to TP3).");
      return INIT_PARAMETERS_INCORRECT;
     }
   if(InpRiskPercent <= 0 || InpRiskPercent > 5)
     {
      Print("[ATR-EW] InpRiskPercent must be in (0, 5].");
      return INIT_PARAMETERS_INCORRECT;
     }
   FolderCreate("ATR_EW", FILE_COMMON);
   FolderCreate("ATR_EW\\state", FILE_COMMON);
   FolderCreate(InpInboxFolder, FILE_COMMON);

   g_trade.SetExpertMagicNumber(InpMagic);
   g_trade.SetDeviationInPoints(InpMaxSlippagePoints);
   g_trade.SetAsyncMode(false);

   if(InpResetDrawdownPause)
     {
      GlobalVariableDel(GvPause());
      GlobalVariableSet(GvPeak(), AccountInfoDouble(ACCOUNT_EQUITY));
      Print("[ATR-EW] drawdown pause cleared and equity peak reset. Set InpResetDrawdownPause=false again.");
     }
   LoadProcessed();

   g_hedging = (AccountInfoInteger(ACCOUNT_MARGIN_MODE) == ACCOUNT_MARGIN_MODE_RETAIL_HEDGING);
   PrintFormat("[ATR-EW] init: login=%s source=%s mode=%s account=%s processed=%d",
               g_login, EnumToString(InpSource), EnumToString(InpExecMode), g_hedging ? "hedging" : "netting",
               ArraySize(g_processed));
   if(!g_hedging)
      Print("[ATR-EW] netting account: entries are refused while ANY position exists on the symbol.");
   if(DrawdownPaused())
      Print("[ATR-EW] drawdown pause is ACTIVE — no new entries until reset.");

   EventSetTimer(1);
   return INIT_SUCCEEDED;
  }

void OnDeinit(const int reason)
  {
   EventKillTimer();
   GlobalVariablesFlush();
  }

void OnTimer()
  {
   ManagePositions();
   UpdateEquityPeak();

   if(TimeLocal() - g_lastPoll >= InpPollSeconds)
     {
      g_lastPoll = TimeLocal();
      if(InpSource == SOURCE_FILE)
         PollFiles();
      else
         PollHttp();
     }
   if(TimeLocal() - g_lastCleanup >= 60)
     {
      g_lastCleanup = TimeLocal();
      CleanupGlobals();
     }
  }

void OnTick()
  {
   // Faster reaction to TP1/TP2 on the chart symbol; other symbols are handled by the 1s timer.
   ManagePositions();
  }
//+------------------------------------------------------------------+
