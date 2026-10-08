# OurKids dashboard — open defects
Rule: nothing is ticked without rendering it in the harness and seeing the number.
A pass = pick the top unticked item, fix, verify, deploy, tick, write what was measured.

## Known open
- [x] GA4 channel split — DONE v84. The collector already pulled a source-based split
      (pull_ga4_touch) and NOTHING rendered it. Card added on Traffic & creative.
      Measured, Egypt, 60d, last touch: Meta 61.0% of sessions at 0.69% CVR;
      Google 16.6% at 1.17%; Dark social 7.2% at 1.40%; Direct 6.8% at 1.26%;
      TikTok 2.1% at 0.30%. Total 1,290,075 sessions, 0.88% CVR, E£15,295,486.
      Dark social OUTRANKS Direct and converts best of the big three — it is
      facebook.com referrals, not ad traffic, and is kept out of Meta's rows.
      First/last touch toggle included. Not GA4 channel grouping, by design.
- [~] Prior-period audit — tools/prior.js written (BROWSER audit; a static scan cannot see
      rendered cells). FIRST VERSION WAS WRONG: its delta regex missed a plain signed
      percentage, so it flagged "Best sellers" (which has a VS PRIOR column reading
      "+66% / +E£238,018") as having none. Fixed; flags fell 27 -> 11.
      Fixed v85: "Keywords — where the click money goes" had 84 numbers and no prior at
      all. It read the FIXED keyword pull while O.ggrain.kw carries the same keywords
      with a 60-day daily series — joined on name, both halves summed. Measured:
      our kids E£45,594 spend ▲30%, 38,958 clicks ▲29%, ROAS 34.53× ▲11%.
      STILL OPEN, and each needs its own judgement:
      - gg "Search terms that converted nothing" (56 cells) — search-term feed ships a
        fixed window with no prior. Needs a collector change, not a UI one.
      - gg "Where the auction was lost" — auction share is lifetime-of-campaign; Google
        ships no daily version. Structural, already stated on the tab.
      - gg "Where the account actually serves" (18) — RULED: no honest prior available.
        It is the serving-NETWORK split from a fixed 60-day pull; the daily feeds are per
        CAMPAIGN and campaign type is a different taxonomy (Shopping serves on the search
        network), so deriving one would be a guess. The card already states its window.
      - tk tables (110 + 10 cells) — no daily series at all. Structural; the new TikTok
        strip and the "why no decomposition" card already say so.
      - mt budget framework — DONE v86. It followed the date box and still showed no prior,
        so a share could move and the table looked identical. Both windows summed from the
        same daily feed. Measured: Testing 29% (was 22%, up 27%), Scaling 43% (was 51%,
        down 15%), Catalog 11% (was 7%, up 67%). ALSO fixed a standing-rule breach: it
        said "under by 11 points" — points are banned; now "28% below the floor".
- [x] kpiStrip coverage — RULED v87, no strip added, and that is the right answer:
      - mi "All platforms side by side" ALREADY opens with a full account comparison
        (spend, CPM, CTR, CVR, CPC, value, ROAS) WITH prior deltas. A strip would duplicate
        it. Left alone.
      - bg "New launches" — these ads are new by definition, so a prior-period strip would
        be blank on every tile. Left alone.
      - ao "Ads -> branches" — the tab's unit is the branch, not the account; the branch
        tables already lead. Left alone.
      FOUND AND FIXED INSTEAD: the mi table claimed Google offline value was
      "none reported". That is false — Google books store-sale uploads (UPLOAD_CLICKS),
      measured E£415,864. It is missing from THAT table because the table reads the daily
      ad feed where Google's ofv is zero on every campaign; the money only exists in the
      fixed 60-day conversion-action pull. Mixing a fixed window into a date-box row would
      be worse, so the omission stays and the claim is corrected: now reads
      "not in this feed — E£415,864 over the fixed 60d pull".
- [x] Portfolio Repeat % — DONE v89, and better than the caveat. The caveat did still render,
      but RT_REP30 already held a 30-day repurchase rate for all ten scopes, so a tenure-free
      repeat was available and I was only apologising for the biased one.
      New "Repeat 30d" column. Measured, and it REVERSES the lifetime ranking:
        New Cairo  lifetime 43.8%  ->  30d 10.2%
        Smouha     lifetime 43.0%  ->  30d 15.5%   (best, not mid-table)
        Nasr City  lifetime 39.7%  ->  30d  9.6%   (worst)
      Lifetime says New Cairo beats Smouha; on the same clock Smouha is 52% ahead of it.
      Lifetime repeat was measuring age, not loyalty.
- [x] Window-label sweep — DONE v88. tools/window.js committed (runtime audit; the dates are
      rendered, not in source). Swept 15 tabs. The detector was validated by disabling the
      exemption and confirming it surfaces the known fixed-window cards.
      3 flags, 2 false positives (a single date printed twice — "TikTok stops at 2026-08-31"
      — reads as a range; checked the text before acting).
      1 GENUINE, fixed: "New audience vs warm vs existing" printed 2026-08-07 → 2026-10-05
      and never said it ignores the date box, so picking L7D silently returned 60 days.
      Now: "a FIXED window from the targeting pull, not your date box. Picking a shorter
      range will not narrow these rows." Re-swept: flag clears.

- [x] Meta per-ad feed was 21.3% short of the account — DONE v92. Started as a cross-source
      agreement check (does the per-ad feed sum to the account series?) and Google answered
      to E£1 while Meta did not:
        28 Sep → 4 Oct   account E£198,386   sum of O.mads E£156,155   gap E£42,231 (-21.3%)
        trailing 30d     account E£861,667   sum of O.mads E£759,446   coverage 88.1%
      MEASURED cause, not inferred: two filters in pull_meta_ads(), a 60-day `spend < 1000`
      floor and a flat per-account top-120, were binding simultaneously — the payload held
      exactly 120 ads on Ourkids EGP and the cheapest kept ad had spent E£1,020. Any card
      that summed the ad feed and read as "the account" understated spend and overstated
      ROAS. Per-ad figures were never wrong; each ad carries its own spend and value.
      Two fixes, both at the scope of the problem rather than the symptom:
      1. Collector: floor 1000 → 150, and the flat cap becomes a FLOOR on what is kept —
         keep taking ads top-down by spend past 120 until the kept set covers 97% of that
         account's own ad-level spend, hard-capped at 400/account so the payload cannot run
         away. Self-tuning, and it stamps XTRA["madsCov"] with what it actually achieved.
      2. Page: kpiStrip() — the ONE shared headline builder both ad tabs use — now compares
         its own windowed ad sum against O.ad.mspend / O.ad.gspend over the same dates and
         prints the coverage. Renders "Covers 78.7% of the account … leaving E£42,231 in a
         tail of ads this feed does not carry" on Meta and "Covers the account … 100.0%" on
         Google. It does NOT claim a ROAS direction: the missing tail carries its own value
         as well as its own spend, so which way the ratio is biased is not known from this.
      tools/coverage.js committed. ~30 sites iterate O.mads; patching each, or injecting a
      synthetic residual row into the shared array, would have been the symptom-scoped fix.

- [x] Two different Meta CVRs on one tab — DONE v92.1. Fell out of extending the coverage
      check to value/orders/clicks: spend, value, orders and impressions were all short by
      about the same 22% (consistent with the dropped-ads tail), but CLICKS were short by 48%.
      That is not coverage. Falsifying test, run before writing a cause: if the tail of
      missing ads explained it, clicks/account would equal spend/account. Measured 54.2% vs
      78.7% — so the two series part by DEFINITION, and the direction says which way.
      MEASURED cause: the account series uses Meta's `clicks` (every click — likes, comments,
      profile taps, image expands); the per-ad feed has always used `outbound_clicks`. Same
      tab, same window, 28 Sep → 4 Oct:
        account-series basis   Meta CVR 0.74%   CPC E£1.63
        per-ad-feed basis      Meta CVR 1.13%   CPC E£2.37
      Nothing on the page said which was which. Fixes:
      1. Collector now also pulls account-level outbound clicks as `moclk`.
      2. boot() aliases O.ad.mclk → O.ad.moclk once it syncs, keeping all-clicks on
         O.ad.mclkAll. One line, so all five click sites switch at once; every one of them
         is a CPC, a CVR or a "clicks fell, why" diagnostic and all three want the clicks
         that reached the site. Patching the five call sites was the symptom-scoped version.
      3. Both cards now name the definition they used, and say so while moclk is unsynced.
- [x] Coverage keep must protect the SHORT window — DONE v92.1. The first fix targeted 97% of
      SIXTY-DAY spend and bought only 90.7% of the trailing seven days (feed E£174,054 against
      account E£191,967). The tail is not evenly spread: it is new and small ads, which weigh
      far more in a 7-day window than in a 60-day total. The keep now ranks on 60d share PLUS
      7d share and requires both coverages (99% / 98%), floor E£50, cap 400/account.
      Ads kept on Ourkids EGP: 120 → 164 after the first fix, re-measuring after this one.

- [x] Prior period, the remaining genuine gaps — DONE v92.2–92.4. The browser sweep flagged
      38 tables; a second filter (does the card SAY it is lifetime or a fixed-window pull?)
      cut that to 5, and of those 3 were false positives whose prior lives in a COLUMN
      ("Prior | Now | Change | %"), not in each cell. prior.js v3 encodes both rules so they
      do not have to be re-read every sweep. The real finds:
      * Channel split (Where the traffic comes from) was a FIXED 60-day GA4 aggregate — it
        ignored the date box AND had no daily series behind it, so 54 cells had nothing to
        compare to. Collector now sends four daily series per channel; the card windows to
        the date box and carries a prior, and says which of the two it is doing.
      * Paid performance (Meta vs Google vs TikTok): impressions, clicks, offline value and
        total ROAS printed no move although PP[k].imp / .clk / .off / .rot were already
        computed. Four columns, zero new data.
      * Blended claim vs ledger: every row is a week, so the comparison sat one row up and
        the reader did the arithmetic. Week-over-week now printed, and a part week is
        suppressed on BOTH sides — a full week against a 2-day opening stub was reading +234%.
      * Audiences in observation is a genuinely fixed 30-day Google pull with no series
        behind it. It now says that, which was the agreed answer for this class.
- [x] Headline row tool-wide — DONE v92.4. kpiStrip was on 2 of the tabs that read the per-ad
      feed. Meta, Campaigns and Money desk opened straight into detail; all three now start
      with the same seven figures in the same order, each against the prior window, with the
      feed coverage stated underneath.
- [x] Two shortfalls, two fixes — DONE v92.4. madsCov (what the collector KEPT of what it
      PULLED) is now printed next to the page's own figure (what the kept set is of the
      ACCOUNT). If the collector kept ~all of its pull and the page still reads short, the
      money never arrived in the pull — rate limits or an abandoned account chunk — and
      widening the keep cannot find it. Those need opposite fixes, so they are named apart.
- 46 tabs rendered in sequence after all of the above: zero console errors, zero throws.

- [x] O.otruth was carried on every sync and read by NOTHING — DONE v92.5. Seventh time this
      session the fix was "the data was already there". It is the only UNMODELLED
      new-versus-repeat split in the payload: Shopify sets customerOrderIndex on every order
      it takes (1 = first ever), so the call needs no identity match, no consent and no
      modelling, and the visit utm gives the channel. 5,566 orders, 1 unlabelled. Every other
      new-customer figure in the tool is either modelled (Google can only label a conversion
      when it identifies the customer) or in-store only (Meta nc/ncv), so this is the one
      that checks the others. Rendered on Traffic Routing, with the Google Ads campaign rows
      (which join on campaign ID) as a drill. What it says, measured:
        META - FACEBOOK  1,699 orders  69.9% new   vs  GOOGLE PAID  1,126 orders  55.0% new
        672 orders worth E£853,453 arrived UNTAGGED — no platform can be credited or blamed
      Two bugs in my own first version, both caught by clicking rather than by reading:
      O.gads[].cmp is the campaign TYPE not its name, so the drill printed "SEARCH" twice and
      named nothing (match on .id, read .n); and the verdict compared META - FACEBOOK against
      DIRECT / NONE as "the same pound spent on those two" — nobody buys direct traffic, so
      the ranking is now restricted to channels there is a budget for.
- [x] A half-filled carry-forward dict was dropping the other half — DONE v92.5.
      `XTRA.get(k) or prev.get(k)` reads as "this run's value, else last run's" and is not:
      a dict that only half filled this run is still truthy. pull_pos_customers() is skipped
      whenever the branch window already reaches today ("pos customers carried forward" in the
      run log), so XTRA["lag"] held only {"shop": ...} — the Stores rows vanished from "Time
      between orders" and its own subtitle printed "half of store repeat gaps fall inside
      — days". cube and jour had each already been hand-patched for exactly this, which is how
      you know it recurs, so the fix is one _xm() helper merging per key, applied to lag, dec
      and metaCC. tools/carry.js committed: a key XTRA writes at more than one site may never
      fall back with `or`. The card also now says WHY the stores half is absent.

- [x] New customers BY CAMPAIGN and by campaign TYPE — DONE v92.6, asked for directly
      ("how much does branding do vs shopping, of all"). The first version answered the wrong
      question: it printed new % WITHIN each campaign, and a campaign can be 70% new and still
      buy almost nobody. The column that answers it is each campaign's share of ALL the new
      customers the business got, so that is what the table sorts on.
      Measured Sep 5 → Oct 5 2026, of 3,243 new customers in total (Google only so far):
        SHOPPING          6 campaigns   361 new   58.3% new within type   11.1% of ALL new
        BRAND             1 campaign    121 new   49.8% within            3.7% of ALL new
        PERFORMANCE MAX   2 campaigns   102 new   53.4% within            3.1% of ALL new
        SEARCH — DSA      1 campaign     33 new   46.5% within            1.0% of ALL new
        SEARCH non-brand  1 campaign      2 new  100.0% within            0.1% of ALL new
        ALL TAGGED                      619 new                         19.1% of ALL new
      So shopping buys 3.0x the first-time customers brand does. Brand also has the lowest
      new-share of any real spender, which is CONSISTENT WITH brand harvesting demand that
      already exists — not proof of it; the falsifying test is a brand holdout and there
      has not been one. See [[gads-pmax-brand-leak]].
      Two collector/page gaps closed on the way:
      * bucket() in pull_order_truth has ALWAYS returned the utm campaign for Meta and TikTok
        and the aggregation kept it only for Google, so the one unmodelled new-customer figure
        in the payload existed per Google campaign and nowhere else. Now byCampaign[channel].
      * Meta has no campaign type that answers brand-vs-shopping (the objective is BUY on
        nearly all of them). Classifying from the NAME was guessing: four of six real names
        ("Back2school s26", "EVER GREEN CONTENT", "Partnership ads", "SHAFI NEW ABO CREATIVE
        TESTING") say nothing about who they target. O.audMix.cmp already carries the ad set
        TARGETING split per campaign id ({n,e,x} spend fractions, 16 campaigns, coverage 1.01),
        so the split is measured; a campaign absent from it falls back to its name and the row
        says "(from name)", and one whose name says nothing reads "targeting not known".

- [x] Momentum: L3D vs prior 3D and L7D vs prior 7D — DONE v93, asked for directly. The tool
      only ever showed ONE window (whatever the date box said) and its prior, so "is this
      turning" took two visits and a memory. Three things the first build got wrong:
      * GRAIN. Per-ad it printed "0 climbing, 0 fading" on 58 of 62 rows. Measured why: the
        median AD takes 3 purchases in a 3-day window, the median ad set 7, the median
        CAMPAIGN 26 — only 9 of 62 ads clear ten conversions on both sides. The horizon was
        fine, the grain was wrong. Defaults to campaign with an Ad set / Ad toggle, and the
        bar prints the median conversion count at the chosen grain.
      * THE PART DAY. O.partial is 2026-10-05 and O.fullEnd is 2026-10-04; the part day held
        E£21,691 against ~E£30,000 on full days, so ending there would have read as a fall on
        every ad at once. Both windows end on fullEnd — another key carried on every sync and
        read by nothing until now.
      * THE MATURATION DRAG WAS TESTED, NOT ASSUMED. The house rule is never to judge data
        younger than four days. Six consecutive 3-day blocks read ROAS 5.89 / 5.46 / 4.86 /
        4.84 / 5.31 / 3.99 newest to oldest — the NEWEST is the highest, the opposite of what
        a maturation drag predicts. So it is not claimed. Arrows are suppressed and replaced
        by the conversion counts wherever either side is under 10.
- [x] Online / in-store / combined ROAS separated — DONE v93.1. The momentum card read `pv`
      only and never said so. Measured, Meta, last 7 complete days: online E£1,062,843 at
      5.39x and IN-STORE E£936,796 at 4.75x — the in-store leg is 88% as large, so an
      online-only ROAS halves the reported return, and the two legs move differently (over the
      3-day pair online rose 28% while in-store rose 76%).
      It changes verdicts, not just levels: SHAFI NEW ABO CREATIVE TESTING reads "climbing on
      both" on online-only and "7d down, last 3 up — recovering" on combined (12.33x, down 23%).
      Scope toggle online / in-store / combined, the ROAS column headers name the leg they are
      showing, and ALL THREE legs are printed at account level underneath so the toggle cannot
      hide one. Combined carries the matching-not-lift caution. Google has no toggle and says
      why: its daily feed has no offline field at all (d holds only sp/pv/pur/imp/clk, ofv is 0
      across the whole 60 days) — that money lives only in the fixed 60-day conversion-action
      pull.

## Tool-wide audit, 9 Oct 2026

Ran every checker, swept all 46 tabs in a browser, and reconciled the payload against the
page both ways. What it found:

- **CLEAN**: 46 tabs render with zero console errors and zero throws. No NaN, no undefined,
  no `[object Object]`, no Infinity, no raw floats anywhere. Coverage green on every feed
  (Meta spend 99.9% / value 99.9% / orders 99.9% / clicks 100.0%, Google 100.3%). contrast,
  adsplit, carry and nocoef all pass. Dead payload keys down to two metadata strings.
- **FOUND AND FIXED — promoCard read a key that has never existed.** It guarded on
  `O.promo.codes`; the collector emits `on` (56 codes) and `off` (76). So the card printed
  "waiting for the first run that carries promo data" permanently while **116 codes and
  E£12.5M of revenue** sat in the payload and rendered fine on the Codes tab. Eighth time
  this session the bug was that nothing read what the collector sent.
- **tools/ghost.js committed** — the inverse of unused.js: fields the PAGE READS that the
  PAYLOAD NEVER SENDS. unused.js finds data nobody renders; ghost.js finds renderers reading
  a field that does not exist, which is worse because the card does not break, it quietly
  shows its empty state on live data. Strips comments and skips runtime-assigned keys
  (O.ad.mclkDef is set by boot(), not the collector). Reads clean now.
- **FOUND AND FIXED — my own regression, minutes old.** The replacement discount-codes card
  summed the whole promo window and carried no prior at all. The prior sweep caught it in
  the same session. It now follows the date box with the equal span before it, and the first
  thing it says is that giveaway is **up 125%** while coded revenue is up 18%.
- **TWO COLLECTOR CHANGES WERE CODE-LIVE BUT DATA-DEAD.** `pull_vendors()` and
  `pull_shop_lines()` sit behind `if heavy:`, so the 6-wide vendor daily series (shops /
  Shopify / MOA split) and the product quota raise (8 → 40 per vendor, daily cap 800 → 2400)
  had not reached the payload — the last heavy crawl ran BEFORE those pushes. The page was
  correctly showing its fallback, but I had reported them as done. Heavy crawl dispatched.
- **STILL OPEN** (flagged, not fixed): otDrill shows "vs prior value" with no attribution
  behind it, so decomp.js reads 9 of 10. Nine tables carry six or more figures with no prior
  period — the branch scoreboard (89 cells), vendor capital (98), three branch tables on the
  retail P&L, and the money-desk burn list. Several of those are structurally levels rather
  than trends, but none of them say so, which is the standard this tool is held to.

## Checks that must stay green
- node tools/contrast.js
- node tools/decomp.js      (8/8)
- node tools/adsplit.js
- node --check on both inline scripts
- node tools/coverage.js okv/data.js 7,30   (spend/value/orders/clicks vs the account series, >=95%)
- node tools/carry.js             (multi-site XTRA keys merged per key, not with `or`)
- node tools/ghost.js <data.js>       (fields the page reads that the payload never sends)
- node tools/nocoef.js               (no r, no R2, no strong/moderate/weak)
