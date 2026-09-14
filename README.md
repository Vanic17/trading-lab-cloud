# Trading Lab Cloud

Un esperimento **solo paper trading**: nessun account exchange, nessun denaro reale,
nessuna leva, nessuno short e nessun ordine inviato all'esterno.

Il motore controlla un paniere di 30 crypto rispetto all'euro ogni 4 ore, usando
esclusivamente candele chiuse Kraken. Per rendere l'esecuzione resistente ai ritardi dei
cron GitHub, un controllo leggero parte ogni 15 minuti ma il motore elabora e registra
un nuovo ciclo solo quando sono trascorse almeno quattro ore dall'ultimo ciclo riuscito.
Lo stato del conto virtuale e il report sono versionati nel repository, perciò il ciclo
continua anche a pagina chiusa.

## Avvio e pubblicazione

1. In GitHub apri **Settings → Pages → Build and deployment → Source** e seleziona
   **GitHub Actions**.
2. Apri la scheda **Actions**, abilita i workflow se GitHub lo chiede e lancia una volta
   **Run paper-trading cycle** con `Run workflow`.
3. Il workflow pubblica la dashboard e poi esegue il ciclo automaticamente ogni quattro
   ore (00:00, 04:00, 08:00, 12:00, 16:00 e 20:00 UTC). GitHub può ritardare di qualche
   minuto i cron nelle ore affollate.

La dashboard sarà disponibile su `https://vanic17.github.io/trading-lab-cloud/`.

## Regole EXP-002 · Crypto 30

- Capitale iniziale virtuale: **€500**.
- Massimo 5 posizioni e 20% del capitale per nuova posizione.
- Acquisto solo quando trend 20/50 candele è positivo e RSI(14) è fra 52 e 68.
- Uscita con stop loss al −7%, take profit al +12% o inversione del trend.
- Commissione simulata: 0,26% per ogni acquisto/vendita.
- Le coppie senza dati validi vengono saltate e annotate nel report.

Le regole sono volutamente semplici, deterministiche e versionate: i risultati non sono
una previsione né una raccomandazione di investimento.
