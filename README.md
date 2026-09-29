# SignalBot 📈 — Nasdaq, Or & Actions sur Telegram

Bot Telegram qui t'aide à **comprendre le marché** avant de trader :

| Fonction | Ce que ça fait |
|---|---|
| 🧭 **Type de marché** | Pour le Nasdaq 100 (NQ) et l'or (XAUUSD) : tendance haussière/baissière, **range**, **indécis** ou **volatil**, sur 3 unités de temps (D1, H4, H1), avec une phrase simple sur quoi faire. |
| 🎯 **Signaux d'achat/vente** | Seulement quand plusieurs éléments concordent (tendance H4/D1, repli, RSI, volume…). Chaque signal a un score /100, une entrée, un stop et 2 objectifs. Pénalisé si une annonce importante arrive bientôt. |
| 🚨 **Alerte gros mouvement** | Dès que le prix bouge fort (ex. −0.6 % en 15 min sur NQ), alerte immédiate **avec l'explication** : news récentes, annonce économique qui vient de sortir, dollar, taux, VIX… |
| 📰 **News expliquées** | Les news qui font bouger le marché (Fed, inflation, emploi, tarifs, géopolitique…) sont envoyées **traduites et expliquées en français** : c'est quoi, l'impact possible et pourquoi, et la direction probable pour le Nasdaq 📈/📉 et l'or. |
| ⏰ **Calendrier économique** | Rappel 30 min avant les annonces US à fort impact (CPI, NFP, FOMC…). |
| 📈 **Section actions (CELI)** | Moyen/long terme : « ce titre a ses résultats bientôt et bat souvent les attentes » ou « bonne compagnie qui a beaucoup baissé, zone d'accumulation possible ». |
| ☀️ **Brief du matin** | Chaque jour de semaine : type de marché, annonces du jour, news clés et un plan de match. |

> ⚠️ C'est un outil d'aide à la décision, **pas un conseil financier**. Aucun système ne gagne à tous les coups : gère ton risque.

---

## Menu

Après `/start`, un **menu à boutons avec icônes** reste affiché en bas de Telegram :

```
[ 📊 Marché            ] [ 🎯 Signaux              ]
[ 📰 News              ] [ 🗓️ Calendrier           ]
[ ❓ Pourquoi ça bouge ] [ ☀️ Brief du jour         ]
[ 📈 Opportunités actions ] [ 🔎 Analyser une action ]
[ 👀 Watchlist         ] [ ℹ️ Aide                  ]
```

« Pourquoi ça bouge » propose ensuite Nasdaq / Or / Les deux, et la Watchlist a des boutons ➕ Ajouter / ➖ Retirer / 📈 Scanner. `/menu` réaffiche le menu si tu l'as caché.

## Commandes

| Commande | Description |
|---|---|
| `/marche` (ou `/marche nq`, `/marche or`) | Type de marché actuel |
| `/signaux` | Meilleur setup du moment pour chaque marché |
| `/pourquoi nq` / `/pourquoi or` | Pourquoi ça bouge en ce moment |
| `/brief` | Plan de match du jour |
| `/news` | News importantes des 12 dernières heures |
| `/calendrier` | Annonces US à fort impact cette semaine |
| `/stocks` | Opportunités actions (earnings + grosses baisses) |
| `/stock AAPL` | Analyse complète d'une action (`RY.TO` pour Toronto) |
| `/watchlist`, `/ajouter NVDA`, `/retirer TSLA` | Gérer la liste d'actions suivies |

---

## Installation (10 minutes)

### 1. Créer ton bot Telegram
1. Dans Telegram, écris à **@BotFather** → `/newbot` → choisis un nom.
2. Copie le **token** qu'il te donne (genre `123456:ABC...`).

### 2. (Optionnel mais recommandé) Clé Claude pour les explications
Sans clé, le bot explique les mouvements et les news avec des règles simples (thème de la news, dollar, taux), et les titres restent en anglais.
Avec une clé [Anthropic](https://console.anthropic.com/), Claude **cherche les dernières nouvelles sur le web** et t'explique en français clair ce qui se passe.

### 3. Lancer le bot
```bash
git clone <ce repo> && cd SIGNALBOT
python -m venv .venv && source .venv/bin/activate    # Windows : .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # puis mets ton TELEGRAM_BOT_TOKEN (et ANTHROPIC_API_KEY) dedans
python -m signalbot
```

Ensuite, dans Telegram, envoie **`/start`** à ton bot. Le premier qui fait `/start` devient le propriétaire (le bot est privé). Le bot affiche ton `chat id` : tu peux le mettre dans `TELEGRAM_CHAT_ID` pour verrouiller l'accès.

### Avec Docker
```bash
docker build -t signalbot .
docker run -d --name signalbot --env-file .env -v $(pwd)/data:/app/data --restart unless-stopped signalbot
```

Pour recevoir les alertes 24/7, fais tourner le bot sur un serveur toujours allumé (VPS, Raspberry Pi, Railway, Fly.io…).

---

## Comment ça marche

### Type de marché
Pour chaque unité de temps, le bot regarde :
- **ADX** (force de la tendance) : > 23 = tendance, < 20 = pas de tendance ;
- la **pente de l'EMA50** et la position du prix par rapport aux EMA20/50 ;
- la **largeur des bandes de Bollinger** (compression = range, souvent avant une cassure) ;
- la **volatilité (ATR)** par rapport à son historique (très haute sans direction = marché chaotique) ;
- le nombre de fois que le prix traverse l'EMA20 (zigzag = range).

Puis il combine D1 / H4 / H1 en une phrase : « acheter les replis », « range entre X et Y : acheter bas, vendre haut », « indécis : patience »…

### Signaux (sur H1, filtrés par H4 et D1)
1. **Repli dans la tendance** : tendance H4 haussière, le prix revient toucher l'EMA20, le RSI se « réinitialise » sous 48 puis repart au-dessus de 50 avec une bougie verte.
2. **Rebond sur une borne de range** : range confirmé, prix dans le bas du range, RSI en survente qui remonte, mèche de rejet.
3. **Cassure après compression** : clôture au-dessus du plus haut des 20 dernières bougies avec une bougie de momentum, idéalement après une compression.

Les ventes utilisent exactement la même logique à l'envers. Stop sous le dernier creux (ou 1 ATR), TP1 = 1.5R, TP2 = 3R. Seuls les signaux ≥ `MIN_SIGNAL_SCORE` (70 par défaut) sont envoyés automatiquement, et jamais deux fois dans le même sens en moins de 4 h.

### Actions (moyen/long terme)
- **Earnings bientôt** : résultats dans les 14 prochains jours + bat les attentes ≥ 75 % du temps + compagnie rentable et en croissance + analystes positifs. Le bot rappelle que les résultats restent un pari binaire.
- **Grosse baisse** : ≥ 20 % sous le sommet de 52 semaines **ET** compagnie de qualité (revenus en croissance, rentable, analystes positifs). Bonus si le RSI hebdo est en survente et la valorisation raisonnable. Le bot suggère d'acheter en plusieurs fois (DCA).
- 🍁 Rappel CELI : les dividendes américains subissent une retenue de 15 % non récupérable dans un CELI (pas les titres canadiens `.TO`).

---

## Réglages utiles (`.env`)

| Variable | Défaut | Rôle |
|---|---|---|
| `NQ_TICKER` / `XAU_TICKER` | `NQ=F` / `GC=F` | Tickers Yahoo Finance. `GC=F` (futures or) suit XAUUSD à quelques dollars près. |
| `NQ_MOVE_15M_PCT` / `NQ_MOVE_60M_PCT` | 0.6 / 1.1 | Seuils d'alerte gros mouvement pour le Nasdaq |
| `XAU_MOVE_15M_PCT` / `XAU_MOVE_60M_PCT` | 0.45 / 0.9 | Seuils pour l'or |
| `MIN_SIGNAL_SCORE` | 70 | Plus haut = moins de signaux mais plus sélectifs |
| `MORNING_BRIEF_TIME` / `STOCKS_REPORT_TIME` | 08:45 / 17:15 | Heures des rapports (heure de `TZ_NAME`) |
| `DIP_THRESHOLD_PCT` | 20 | Baisse minimale pour la section « grosse baisse » |
| `STOCK_WATCHLIST` | ~40 titres US + canadiens | Liste d'actions analysées |
| `CLAUDE_WEB_SEARCH` | 1 | Claude vérifie les news en direct sur le web |

## Sources de données
- Prix : Yahoo Finance via `yfinance` (gratuit, léger délai possible).
- News : flux RSS CNBC, MarketWatch, FXStreet, Yahoo Finance, Investing.com.
- Calendrier économique : flux hebdomadaire ForexFactory (annonces USD à fort impact).

## Tests
```bash
pip install -r requirements-dev.txt
pytest
```
