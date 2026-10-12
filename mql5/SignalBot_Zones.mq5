//+------------------------------------------------------------------+
//| SignalBot_Zones.mq5 - zones du bot Telegram SignalBot, à jour     |
//| toutes seules dans MetaTrader 5.                                  |
//|                                                                  |
//| Le bot (sur le même ordinateur) écrit chaque minute               |
//| Common\Files\SignalBot_zones.txt : zones ACHAT / VENTE avec leur  |
//| force (murs gamma, bascule gamma, hier, semaine passée, profil de |
//| volume, SpotGamma, zones IA), le sentiment acheteur / vendeur     |
//| (Myfxbook, COT, tendance, VWAP, gamma, IA) et les prochaines news.|
//| Cet indicateur relit le fichier toutes les 30 secondes et         |
//| redessine tout. Rien à copier-coller.                             |
//|                                                                  |
//| Les prix du bot sont ceux des contrats à terme (NQ, GC) :         |
//| ils sont convertis au prix de ton courtier (NAS100, XAUUSD...).   |
//+------------------------------------------------------------------+
#property copyright "SignalBot"
#property version   "1.00"
#property indicator_chart_window
#property indicator_plots 0

input string InpMarche      = "Auto"; // Marché : Auto, NQ ou XAU
input int    InpGauche      = 80;     // longueur des zones vers la gauche (bougies)
input int    InpDroite      = 15;     // longueur des zones vers la droite (bougies)
input bool   InpPanneau     = true;   // afficher le panneau sentiment + news
input bool   InpAlertes     = true;   // alerte quand le prix entre dans une zone
input string InpFichier     = "SignalBot_zones.txt"; // fichier écrit par le bot (dossier commun)

#define PFX "SBZ_"

string g_mkt = "";
double g_zlo[], g_zhi[];
string g_zside[];
int    g_inside = -1;

//+------------------------------------------------------------------+
string DetectMarket()
{
   if(InpMarche == "NQ" || InpMarche == "XAU")
      return InpMarche;
   string s = _Symbol;
   StringToUpper(s);
   if(StringFind(s, "XAU") >= 0 || StringFind(s, "GOLD") >= 0 || StringFind(s, "GC") == 0)
      return "XAU";
   if(StringFind(s, "NQ") == 0 || StringFind(s, "MNQ") == 0 || StringFind(s, "NAS") >= 0 ||
      StringFind(s, "US100") >= 0 || StringFind(s, "USTEC") >= 0 || StringFind(s, "NDX") >= 0 ||
      StringFind(s, "USTECH") >= 0 || StringFind(s, "US TECH") >= 0)
      return "NQ";
   return "";
}

//+------------------------------------------------------------------+
int OnInit()
{
   g_mkt = DetectMarket();
   EventSetTimer(30);
   Refresh();
   return INIT_SUCCEEDED;
}

void OnDeinit(const int reason)
{
   EventKillTimer();
   ObjectsDeleteAll(0, PFX);
   ChartRedraw();
}

void OnTimer() { Refresh(); }

int OnCalculate(const int rates_total, const int prev_calculated, const datetime &time[],
                const double &open[], const double &high[], const double &low[], const double &close[],
                const long &tick_volume[], const long &volume[], const int &spread[])
{
   CheckZoneEntry();
   return rates_total;
}

//+------------------------------------------------------------------+
string Stars(int force)
{
   string s = "";
   for(int i = 0; i < MathMin(force, 5); i++)
      s += "★";
   return s;
}

void Text(string name, int x, int y, string txt, color clr, int size = 9)
{
   if(ObjectFind(0, name) < 0)
      ObjectCreate(0, name, OBJ_LABEL, 0, 0, 0);
   ObjectSetInteger(0, name, OBJPROP_CORNER, CORNER_LEFT_UPPER);
   ObjectSetInteger(0, name, OBJPROP_XDISTANCE, x);
   ObjectSetInteger(0, name, OBJPROP_YDISTANCE, y);
   ObjectSetInteger(0, name, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, name, OBJPROP_FONTSIZE, size);
   ObjectSetString(0, name, OBJPROP_FONT, "Segoe UI");
   ObjectSetString(0, name, OBJPROP_TEXT, txt);
   ObjectSetInteger(0, name, OBJPROP_SELECTABLE, false);
}

void DrawZone(int n, string side, double lo, double hi, int force, string reasons)
{
   bool   buy = (side == "ACHAT");
   color  clr = buy ? C'30,158,90' : C'217,58,58';
   int    sec = PeriodSeconds();
   datetime t1 = iTime(_Symbol, _Period, MathMin(InpGauche, Bars(_Symbol, _Period) - 1));
   datetime t2 = TimeCurrent() + InpDroite * sec;
   string box = PFX + "Z" + IntegerToString(n);
   ObjectCreate(0, box, OBJ_RECTANGLE, 0, t1, hi, t2, lo);
   ObjectSetInteger(0, box, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, box, OBJPROP_FILL, true);
   ObjectSetInteger(0, box, OBJPROP_BACK, true);
   ObjectSetInteger(0, box, OBJPROP_SELECTABLE, false);
   string lbl = PFX + "T" + IntegerToString(n);
   string txt = side + " " + Stars(force) + "  " + DoubleToString((lo + hi) / 2, _Digits) + " — " + reasons;
   ObjectCreate(0, lbl, OBJ_TEXT, 0, t2, (lo + hi) / 2);
   ObjectSetString(0, lbl, OBJPROP_TEXT, txt);
   ObjectSetString(0, lbl, OBJPROP_FONT, "Segoe UI");
   ObjectSetInteger(0, lbl, OBJPROP_FONTSIZE, 8);
   ObjectSetInteger(0, lbl, OBJPROP_COLOR, clr);
   ObjectSetInteger(0, lbl, OBJPROP_ANCHOR, ANCHOR_RIGHT);
   ObjectSetInteger(0, lbl, OBJPROP_SELECTABLE, false);
}

//+------------------------------------------------------------------+
void Refresh()
{
   ObjectsDeleteAll(0, PFX);
   ArrayResize(g_zlo, 0);
   ArrayResize(g_zhi, 0);
   ArrayResize(g_zside, 0);
   int y = 20;
   if(g_mkt == "")
   {
      Text(PFX + "P0", 10, y, "SignalBot : marché non reconnu (choisis NQ ou XAU dans « Marché »)", clrOrange);
      ChartRedraw();
      return;
   }
   if(!FileIsExist(InpFichier, FILE_COMMON))
   {
      Text(PFX + "P0", 10, y, "SignalBot : fichier introuvable. Lance le bot (Lancer SignalBot.bat).", clrOrange);
      ChartRedraw();
      return;
   }
   int h = FileOpen(InpFichier, FILE_READ | FILE_TXT | FILE_ANSI | FILE_COMMON | FILE_SHARE_READ | FILE_SHARE_WRITE,
                    0, CP_UTF8);
   if(h == INVALID_HANDLE)
      return;

   long   written = 0;
   double ref = 0, ratio = 1.0;
   string sentLabel = "", gamma = "";
   int    score = 0, buyers = 50, nz = 0, ns = 0, nn = 0;
   string reasons[];
   string news[];
   while(!FileIsEnding(h))
   {
      string line = FileReadString(h);
      string f[];
      int k = StringSplit(line, ';', f);
      if(k < 2)
         continue;
      if(f[0] == "TIME")
         written = StringToInteger(f[1]);
      else if(f[0] == "M" && k >= 7 && f[1] == g_mkt)
      {
         ref = StringToDouble(f[2]);
         score = (int)StringToInteger(f[3]);
         sentLabel = f[4];
         buyers = (int)StringToInteger(f[5]);
         gamma = f[6];
         double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
         ratio = (ref > 0 && bid > 0) ? bid / ref : 1.0;
      }
      else if(f[0] == "Z" && k >= 7 && f[1] == g_mkt)
      {
         double lo = StringToDouble(f[3]) * ratio, hi = StringToDouble(f[4]) * ratio;
         DrawZone(nz, f[2], lo, hi, (int)StringToInteger(f[5]), f[6]);
         ArrayResize(g_zlo, nz + 1);
         ArrayResize(g_zhi, nz + 1);
         ArrayResize(g_zside, nz + 1);
         g_zlo[nz] = lo;
         g_zhi[nz] = hi;
         g_zside[nz] = f[2];
         nz++;
      }
      else if(f[0] == "S" && k >= 4 && f[1] == g_mkt)
      {
         ArrayResize(reasons, ns + 1);
         reasons[ns++] = (f[3] == "+" ? "▲ " : "▼ ") + f[2];
      }
      else if(f[0] == "N" && k >= 3)
      {
         datetime t = (datetime)StringToInteger(f[1]);
         if(t > TimeGMT() - 600)
         {
            ArrayResize(news, nn + 1);
            // heure locale de l'ordinateur
            news[nn++] = TimeToString(t + (TimeLocal() - TimeGMT()), TIME_MINUTES) + "  " + f[2];
         }
      }
   }
   FileClose(h);

   if(!InpPanneau)
   {
      ChartRedraw();
      return;
   }
   long age = (long)TimeGMT() - written;
   string head = "SignalBot " + g_mkt + (age > 900 ? "  (bot arrêté ? données de il y a " + IntegerToString(age / 60) + " min)" : "");
   Text(PFX + "P0", 10, y, head, age > 900 ? clrOrange : clrSilver, 9);
   y += 18;
   color sc = score >= 15 ? clrLime : score <= -15 ? clrTomato : clrSilver;
   Text(PFX + "P1", 10, y, "Sentiment : " + sentLabel + "  (" + IntegerToString(buyers) + " % acheteur / " +
        IntegerToString(100 - buyers) + " % vendeur)", sc, 11);
   y += 22;
   for(int i = 0; i < ns; i++)
   {
      Text(PFX + "R" + IntegerToString(i), 18, y, reasons[i], StringFind(reasons[i], "▲") == 0 ? clrLime : clrTomato, 8);
      y += 15;
   }
   if(gamma != "")
   {
      Text(PFX + "G", 10, y + 3, "Gamma " + gamma + (gamma == "positif" ? " : le prix reste souvent entre les zones" :
           " : les cassures vont plus loin"), gamma == "positif" ? clrLime : clrTomato, 8);
      y += 20;
   }
   for(int i = 0; i < MathMin(nn, 4); i++)
   {
      Text(PFX + "N" + IntegerToString(i), 10, y + 3, "⚠ News " + news[i], clrGold, 9);
      y += 17;
   }
   ChartRedraw();
}

//+------------------------------------------------------------------+
void CheckZoneEntry()
{
   if(!InpAlertes)
      return;
   double bid = SymbolInfoDouble(_Symbol, SYMBOL_BID);
   int inside = -1;
   for(int i = 0; i < ArraySize(g_zlo); i++)
      if(bid >= g_zlo[i] && bid <= g_zhi[i])
         inside = i;
   if(inside >= 0 && inside != g_inside)
      Alert("SignalBot : ", _Symbol, " entre dans une zone ", g_zside[inside], " (",
            DoubleToString(g_zlo[inside], _Digits), " - ", DoubleToString(g_zhi[inside], _Digits), ")");
   g_inside = inside;
}
//+------------------------------------------------------------------+
