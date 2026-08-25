#!/usr/bin/env python3
"""Install (or re-install) the Audit tabs into docs/ourkids/index.html.

IDEMPOTENT BY DESIGN. index.html is edited by several sessions at once, and more than
once a session has pushed it from a stale working copy and silently deleted these tabs.
Rather than fight that by hand, this script re-applies the hooks on top of whatever is
currently in the file — it never removes anyone else's work — and a workflow runs it
after every push. Run it twice and the second run is a no-op.

Four hooks plus the code:
  1. the four tab containers
  2. the Audit entry in TABGROUPS
  3. the container toggle + render dispatch inside tab()
  4. the TBT entry, which is what gives the tabs the universal date bar
  5. <script src="audit_tabs.js"> — the tab code itself lives in its own file, so a
     clobber costs one line instead of 60KB

Exit 0 and prints CHANGED or UNCHANGED.
"""
import os, re, sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
IDX = os.path.join(ROOT, "docs", "ourkids", "index.html")

CONT = ('<div id="auC" class="hide"><div class="banner" id="bn_au"></div><div class="grid" id="g_au"></div></div>'
        '<div id="avC" class="hide"><div class="banner" id="bn_av"></div><div class="grid" id="g_av"></div></div>'
        '<div id="agC" class="hide"><div class="banner" id="bn_ag"></div><div class="grid" id="g_ag"></div></div>'
        '<div id="aqC" class="hide"><div class="banner" id="bn_aq"></div><div class="grid" id="g_aq"></div></div>')
GROUP = " ['Audit',[['au','Commercial'],['av','Vendor Capital'],['ag','Listing Gap'],['aq','Demand Gap']]]\n];"
TOGGLE = ("['au','av','ag','aq'].forEach(function(x){var e=document.getElementById(x+'C');"
          "if(e)e.classList.toggle('hide',t!==x);});")
DISPATCH = "if(t==='au')return rAU();if(t==='av')return rAV();if(t==='ag')return rAG();if(t==='aq')return rAQ();"
TBT = "['au','auC'],['av','avC'],['ag','agC'],['aq','aqC']];"
SCRIPT = '<script src="audit_tabs.js"></script>'


def install(s):
    changed = []

    if 'id="auC"' not in s:
        m = re.search(r'<div id="trC" class="hide">.*?</div></div>', s, re.S)
        if not m:
            raise SystemExit("FATAL: cannot find the trC container to anchor on")
        s = s[:m.end()] + CONT + s[m.end():]
        changed.append("containers")

    if "['Audit',[['au','Commercial']" not in s:
        m = re.search(r"\n\];", s[s.index("const TABGROUPS=["):])
        if not m:
            raise SystemExit("FATAL: cannot find the end of TABGROUPS")
        at = s.index("const TABGROUPS=[") + m.start()
        s = s[:at] + ",\n" + GROUP + s[at + 3:]
        changed.append("tab group")

    if TOGGLE not in s:
        a = "document.getElementById('ceC').classList.toggle('hide',t!=='ce');"
        if a not in s:
            raise SystemExit("FATAL: cannot find the tab() toggle chain")
        s = s.replace(a, a + TOGGLE, 1)
        changed.append("toggles")

    if DISPATCH not in s:
        a = "if(t==='si')return rSI();"
        if a not in s:
            raise SystemExit("FATAL: cannot find the tab() dispatch chain")
        s = s.replace(a, DISPATCH + a, 1)
        changed.append("dispatch")

    if "['au','auC']" not in s:
        a = "['cs','csC'],['ab','abC']];"
        if a not in s:
            raise SystemExit("FATAL: cannot find TBT")
        s = s.replace(a, a[:-2] + "," + TBT, 1)
        changed.append("date bar")

    if SCRIPT not in s:
        i = s.rindex("</body>")
        s = s[:i] + SCRIPT + "\n" + s[i:]
        changed.append("script tag")

    return s, changed


def verify(s):
    """Cheap structural gate. The first version of this script silently ate the `];`
       that closes TABGROUPS and produced a file that would not parse -- every substring
       check still passed. So assert the SHAPES, not just the presence of markers."""
    i = s.index("const TABGROUPS=[")
    tail = s[i:i + 4000]
    if "\n];" not in tail:
        raise SystemExit("FATAL: TABGROUPS is no longer terminated by ];")
    if tail.index("['Audit',") > tail.index("\n];"):
        raise SystemExit("FATAL: the Audit group landed outside TABGROUPS")
    for need in ('id="auC"', "['au','auC']", "if(t==='au')return rAU();", SCRIPT):
        if need not in s:
            raise SystemExit("FATAL: hook missing after install -- " + need)
    if s.count(SCRIPT) != 1:
        raise SystemExit("FATAL: audit_tabs.js included %d times" % s.count(SCRIPT))
    return True


def main():
    src = open(IDX, encoding="utf-8").read()
    out, changed = install(src)
    # a second pass must be a no-op, or the guard would grow the file on every run
    twice, again = install(out)
    if again:
        raise SystemExit("FATAL: not idempotent, second pass wanted %s" % again)
    verify(out)
    if out != src:
        open(IDX, "w", encoding="utf-8").write(out)
        print("CHANGED:", ", ".join(changed))
    else:
        print("UNCHANGED")


if __name__ == "__main__":
    main()
