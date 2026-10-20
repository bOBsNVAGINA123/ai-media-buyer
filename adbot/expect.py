"""What a budget move should do to REAL gross profit per day, with the odds.

Measured 20 Oct 2026 on 14 Oct 2025 - 13 Oct 2026 (re-run monthly):
* Account level, real Odoo revenue per channel (RIS moments), week-to-week changes with Google spend/off,
  fulfillable stock, payday and last-year demand controlled: each extra Meta E£ at E£37.3k/day brings
  E£4.24 online revenue (95% CI 2.86-5.63) and E£17.16 in-store revenue (10.36-23.96).
  Held when dropping the biggest spend swings, any single month (in-store 14.66-19.13) and on a placebo
  (spend 7 days LATER does not predict today's sales). Not ruled out: other marketing timed with Meta pushes.
* Ad-set level: the blended ROAS forecast still ran high by 8-13% (BIAS) and its spread was too narrow;
  x1.65 made the 80% range hold 79-83% of the time on weeks it had not seen (604 ad-set-weeks).
* An ad set's share of the account's extra revenue follows its forecast ROAS relative to the account's
  (INFERRED - the only per-ad-set signal; Meta's in-store claims for the in-store part).
"""
import math, random

CAL = {"spend_ref": 37309.0,
       "on": (4.24, 0.71, 0.80),     # revenue per extra Meta E£ at spend_ref: mean, s.e., elasticity
       "st": (14.70, 3.87, 0.64)}   # in-store: the most conservative robust read (back to school, White Friday, Ramadan/Eid weeks left out; 17.16 with them)
# purchases last week >= p: (median log miss, p10, p90 of log miss around it, already x1.65)
BIAS = ((0, -0.078, -1.472, 1.150), (10, -0.116, -0.973, 0.834), (20, -0.117, -0.812, 0.819), (50, -0.133, -1.271, 0.816))
CALIB_TXT = ("80% ranges held 79-83% of the time on weeks the model had not seen; "
             "the account model missed weekly gross profit by a median 8.9%")


def miss(purch):
    return next((b, lo, hi) for p, b, lo, hi in reversed(BIAS) if purch >= p)


def simulate(spend_day, k, r_on, r_st, acc_on, acc_st, s_now, purch, curve_b, curve_sd, m_on, m_st, season=(1.0, 1.0), n=4000, seed=7):
    """Net E£/day from moving daily spend spend_day -> k * spend_day.
    r_on / r_st: this owner's forecast 7-day-click ROAS online / in-store; acc_*: the account's, same basis."""
    rnd = random.Random(seed); bias, lo, hi = miss(purch)
    net, onl, d_on_all, d_st_all = [], [], [], []
    for _ in range(n):
        z = rnd.gauss(0, 1)
        f = math.exp(bias + z * ((-lo if z < 0 else hi) / 1.2816))          # split-normal miss of the ROAS forecast
        con = max(rnd.gauss(*CAL["on"][:2]), 0) * (s_now / CAL["spend_ref"]) ** (CAL["on"][2] - 1) * season[0]
        cst = max(rnd.gauss(*CAL["st"][:2]), 0) * (s_now / CAL["spend_ref"]) ** (CAL["st"][2] - 1) * season[1]
        b = min(max(rnd.gauss(curve_b, curve_sd), 0.2), 0.98)
        span = spend_day * (k ** b - 1) / b                                  # spend-weighted marginal over the move
        d_on = con * (r_on * f / acc_on if acc_on else 0) * span * m_on
        d_st = cst * (r_st * f / acc_st if acc_st else 0) * span * m_st
        ds = spend_day * (k - 1)
        net.append(d_on + d_st - ds); onl.append(d_on - ds); d_on_all.append(d_on); d_st_all.append(d_st)
    q = lambda xs, p: sorted(xs)[int(p * (len(xs) - 1))]
    return {"k": k, "spend": round(spend_day * (k - 1)), "net_p10": round(q(net, .1)), "net_p50": round(q(net, .5)), "net_p90": round(q(net, .9)),
            "p_pays": round(sum(x > 0 for x in net) / n, 2), "online_p50": round(q(onl, .5)), "online_p_pays": round(sum(x > 0 for x in onl) / n, 2),
            "gp_online": round(q(d_on_all, .5)), "gp_instore": round(q(d_st_all, .5))}


def season_index(shop, pos, today):
    """Expected demand over the next 14 days vs this year's daily average, online (daily Shopify) and in-store
    (monthly branch totals) = (this year's last 28 days / this year's last 365) x (last year's next 14 days /
    last year's 28 days before them). Scales the account's extra-pound return, which was measured at the yearly
    average. The last-year ratio, tested over 48 weeks, cut the 2-week forecast miss 31% -> 20% online, 21% -> 13% in-store."""
    import datetime as dt
    D = lambda k: today + dt.timedelta(days=k)
    ly = lambda d: d - dt.timedelta(days=364)
    past28 = [D(-k) for k in range(1, 29)]; yr = [D(-k) for k in range(1, 366)]; nxt = [D(k) for k in range(14)]
    def mdays(d):   # the current month holds month-to-date sales through yesterday
        if (d.year, d.month) == (today.year, today.month):
            return max(today.day - 1, 1)
        return ((d.replace(day=28) + dt.timedelta(days=4)).replace(day=1) - dt.timedelta(days=1)).day
    on = lambda d: shop.get(d.isoformat())
    st = lambda d: (pos.get(d.strftime("%Y-%m"), 0) / mdays(d)) or None
    def avg(f, days):
        v = [f(d) for d in days]; v = [x for x in v if x]
        return sum(v) / len(v) if len(v) >= 0.8 * len(days) else None
    r = []
    for f in (on, st):
        level = avg(f, past28), avg(f, yr)
        shape = avg(f, [ly(d) for d in nxt]), avg(f, [ly(d) for d in past28])
        r.append(min(max(level[0] / level[1] * shape[0] / shape[1], 0.5), 2.0) if all(level + shape) else 1.0)
    return tuple(round(x, 2) for x in r)


def package(s0, s1, season=(1.0, 1.0), m_on=0.2605, m_st=0.3183):
    """Approve everything at once: account spend s0 -> s1 E£/day. Account-level curve (revenue ~ spend^elasticity,
    level set by the measured extra-pound return at spend_ref). Returns net E£/day at the low / mid / high return."""
    out = {}
    for tag, z in (("low", -1.2816), ("mid", 0.0), ("high", 1.2816)):     # 80% range of the measured returns
        net = -(s1 - s0)
        for key, m, sea in (("on", m_on, season[0]), ("st", m_st, season[1])):
            per, se, el = CAL[key]; per = max(per + z * se, 0)
            level = per * CAL["spend_ref"] / el * sea
            net += level * ((s1 / CAL["spend_ref"]) ** el - (s0 / CAL["spend_ref"]) ** el) * m
        out[tag] = round(net)
    return out


def label(p):
    return "very likely" if p >= 0.9 else "likely" if p >= 0.75 else "coin flip" if p >= 0.5 else "unlikely"


if __name__ == "__main__":
    # self-check: an owner exactly at the account's ROAS, small push, should pay (account marginal GP ~E£6.6 per E£)
    r = simulate(1000, 1.2, 5.0, 4.0, 5.0, 4.0, 37309, 30, 0.7, 0.1, 0.2605, 0.3183)
    assert r["p_pays"] > 0.75 and r["net_p50"] > 0, r
    # an owner at a tenth of the account's ROAS should not
    r2 = simulate(1000, 1.2, 0.5, 0.4, 5.0, 4.0, 37309, 30, 0.7, 0.1, 0.2605, 0.3183)
    assert r2["net_p50"] < 0, r2
    # a cut on the weak one should pay
    r3 = simulate(1000, 0.7, 0.5, 0.4, 5.0, 4.0, 37309, 30, 0.7, 0.1, 0.2605, 0.3183)
    assert r3["net_p50"] > 0, r3
    print("ok", r, r2, r3, sep="\n")
