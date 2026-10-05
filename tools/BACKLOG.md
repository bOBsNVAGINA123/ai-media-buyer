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

## Checks that must stay green
- node tools/contrast.js
- node tools/decomp.js      (8/8)
- node tools/adsplit.js
- node --check on both inline scripts
- node tools/coverage.js okv/data.js 7,30   (spend/value/orders/clicks vs the account series, >=95%)
