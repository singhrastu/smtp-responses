#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Build the full SMTP response registry from the authoritative sources.

    python3 build/export_registry.py            # use the cached sources
    python3 build/export_registry.py --fetch    # re-download them first

Fourteen hand-written pages is a stub, not a reference. Somebody who pastes
"5.7.512" or types "452" needs an answer, and there is a bounded, authoritative
set of both:

    RFC 5321 section 4.2.3   the basic reply codes           25 of them
    IANA SMTP Enhanced
    Status Codes registry    the enhanced status codes       81 of them

Both are parsed from the published source and cached under build/sources/, so
the build is reproducible offline and the provenance of every line is checkable.

WHAT THIS ADDS TO THE SOURCE DATA
The registries define what a code *means*. They say nothing about what to do,
which is the only reason anybody looks one up at three in the morning. So every
entry also carries an action from the same vocabulary the bounce classifier
uses, derived from the code's class and subject, plus a note where the derived
answer would be misleading on its own.

Entries are marked with how much is known about them:

    written    a full page exists: real log samples, causes, remediation
    derived    the registry definition plus an action inferred from its class

That distinction is published on the page. A derived action is a sound default.
An action I have worked myself is a tested one. The reference says which it is
rather than letting the two read alike.
"""
import argparse
import csv
import json
import os
import re
import sys
import urllib.request

HERE = os.path.dirname(os.path.abspath(__file__))
SRC = os.path.join(HERE, "sources")
OUT = os.path.join(HERE, "registry.json")

SOURCES = {
    "iana-esc.csv":
        "https://www.iana.org/assignments/smtp-enhanced-status-codes/"
        "smtp-enhanced-status-codes-3.csv",
    "rfc5321.txt": "https://www.rfc-editor.org/rfc/rfc5321.txt",
}

# Microsoft publishes its own enhanced codes, most of which are outside the IANA
# registry entirely: the 5.7.5xx and 5.7.6xx ranges do not exist in RFC 3463.
# The table is fetched into build/sources/microsoft-ndr.md by hand because the
# page is behind a client-rendered doc site; the parser below is what turns it
# into entries, and the cached copy is what makes the build reproducible.
MICROSOFT_SRC = "microsoft-ndr.md"
MICROSOFT_URL = ("https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/"
                 "non-delivery-reports-in-exchange-online/"
                 "non-delivery-reports-in-exchange-online")

# ---------------------------------------------------------------------------
# The classes, from RFC 3463 section 3.2.
CLASSES = {
    "2": ("Success", "The request was accepted."),
    "4": ("Transient", "A temporary failure. The same request may well succeed "
                       "later, so the sender is expected to retry."),
    "5": ("Permanent", "A permanent failure. Retrying the same request is not "
                       "expected to work, and repeated attempts cost sender "
                       "reputation."),
}

# The subjects, from RFC 3463 section 3.3. The second element is what the
# subject means for somebody holding a bounce rather than reading the spec.
SUBJECTS = {
    "0": ("Other or undefined", "The receiver said only which class of problem "
          "it hit, not what the problem was."),
    "1": ("Addressing", "Something about the sender or recipient address."),
    "2": ("Mailbox", "The mailbox exists as an address but cannot take the "
          "message right now, or at all."),
    "3": ("Mail system", "The receiving system itself: capacity, storage, or a "
          "message too large for it."),
    "4": ("Network and routing", "Getting to the destination, rather than "
          "anything about the destination."),
    "5": ("Mail delivery protocol", "The SMTP conversation itself went wrong."),
    "6": ("Message content or media", "The content, encoding or conversion of "
          "the message."),
    "7": ("Security or policy", "A deliberate decision by the receiver: "
          "authentication, reputation, or a rule."),
}

# Action per (class, subject), in the vocabulary the bounce classifier already
# uses. These are defaults derived from the code's structure, not from having
# operated each failure, and they are labelled as derived wherever they appear.
DERIVED_ACTION = {
    ("2", None): ("deliver", "Accepted. Nothing to do."),
    ("4", "0"): ("retry", "Temporary and unspecified. The normal retry schedule handles it."),
    ("4", "1"): ("retry", "Temporary problem with the address. Retry, then suppress if it persists."),
    ("4", "2"): ("retry", "The mailbox cannot take it now. Retry with backoff for a few days, then suppress."),
    ("4", "3"): ("retry", "The receiving system is short of capacity. Retry, and reduce concurrency if it recurs."),
    ("4", "4"): ("retry", "A routing or network problem. Retry, but investigate if it sticks to one destination."),
    ("4", "5"): ("retry", "A protocol-level hiccup. Retry; if it repeats, the two systems disagree about something."),
    ("4", "6"): ("review", "The receiver could not process the content this time. Retrying rarely helps on its own."),
    ("4", "7"): ("throttle", "A policy or reputation decision, held open rather than refused. Slow down for this provider and read the text."),
    ("5", "0"): ("review", "Permanent and unspecified. The accompanying text is the only evidence."),
    ("5", "1"): ("suppress", "The address is wrong or gone. Remove it; retrying costs reputation."),
    ("5", "2"): ("suppress", "The mailbox will not take mail. Suppress it, because dormant addresses become spam traps."),
    ("5", "3"): ("review", "The receiving system refused it outright. Usually message size or a system limit."),
    ("5", "4"): ("review", "Permanent routing failure. Check the destination's DNS before blaming the address."),
    ("5", "5"): ("fix_config", "A protocol error. Something in how the message or session is constructed is wrong."),
    ("5", "6"): ("review", "The content was refused. Change the message, not the rate."),
    ("5", "7"): ("pause", "A security or policy refusal. Stop and read the text before sending more."),
}

# Where the structural default is wrong or too vague to act on. Each of these is
# a judgement about the specific code rather than about its class.
OVERRIDE = {
    "4.2.2": ("retry", "The mailbox is over quota. It often clears. Retry with backoff for "
                       "a few days, then suppress."),
    "4.4.1": ("retry", "No answer from the destination host. A transport failure, not a mail "
                       "failure. Investigate if it sticks to one route."),
    "4.4.5": ("throttle", "The receiver is congested. Reduce concurrency for this provider "
                          "specifically rather than globally."),
    "4.5.3": ("throttle", "Too many recipients in one transaction. Split the envelope."),
    "4.7.0": ("throttle", "Held for policy reasons, temporarily. This is where most rate "
                          "limiting lands, so back off before retrying."),
    "5.1.1": ("suppress", "The mailbox does not exist. Permanent. Suppress immediately: "
                          "repeated delivery to unknown users is the fastest way to lose "
                          "reputation at any provider."),
    "5.1.2": ("suppress", "The destination domain does not exist or takes no mail. Check for "
                          "a typo in the domain, then suppress."),
    "5.1.6": ("suppress", "The mailbox has moved and no forwarding address was given."),
    "5.1.8": ("fix_config", "Your envelope sender is bad. This is your configuration, not "
                            "the recipient's mailbox."),
    "5.2.1": ("suppress", "The mailbox is disabled or not accepting mail. Suppress: these "
                          "turn into spam traps."),
    "5.2.2": ("retry", "Over quota, permanently signalled. Some receivers use 5.2.2 where "
                       "others use 4.2.2, so retry a few times before suppressing."),
    "5.2.3": ("review", "The message is too large for the mailbox. Reduce the message size."),
    "5.3.4": ("review", "The message is too large for the system. Check your maximum message "
                        "size against the receiver's."),
    "5.4.1": ("review", "No answer from the destination and no route to it. Verify the "
                        "domain's MX records exist and resolve."),
    "5.4.4": ("review", "Unable to route. Usually a DNS problem at the destination."),
    "5.5.0": ("fix_config", "The SMTP conversation went wrong. Check what your MTA sent "
                            "immediately before this."),
    "5.7.1": ("pause", "Delivery not authorised. This covers everything from a reputation "
                       "block to a relaying refusal, so the accompanying text decides what "
                       "it actually is."),
    "5.7.13": ("fix_config", "The sending account is disabled. This is your side."),
    "5.7.25": ("fix_config", "The reverse DNS of your sending IP does not resolve forward to "
                             "the same address. Fix rDNS and the forward record."),
    "5.7.26": ("fix_config", "Authentication failed under the domain's DMARC policy. "
                             "Retrying will not help; SPF or DKIM has to pass and align."),
    "5.7.27": ("suppress", "The sender domain has no MX record. Nothing can reply to it."),
}

# Provider-specific responses are not in either registry, so they carry their
# source alongside them and are marked as such.
PROVIDER = [
    {"code": "4.7.28", "provider": "Gmail",
     "text": "Unusual rate of unsolicited mail from your IP",
     "source": "Google Postmaster / SMTP error reference"},
    {"code": "4.7.0", "provider": "Gmail",
     "text": "Temporary block, often reputation-driven",
     "source": "Google SMTP error reference"},
    {"code": "5.7.1", "provider": "Gmail",
     "text": "Message blocked; likely unsolicited",
     "source": "Google SMTP error reference"},
    {"code": "5.7.26", "provider": "Gmail",
     "text": "Unauthenticated mail is not accepted",
     "source": "Google bulk sender guidelines"},
    {"code": "5.7.606", "provider": "Microsoft / Outlook",
     "text": "Access denied, banned sending IP",
     "source": "Microsoft 365 error code reference"},
    {"code": "4.7.500", "provider": "Microsoft / Outlook",
     "text": "Server busy, please try again later",
     "source": "Microsoft 365 error code reference"},
    {"code": "4.7.650", "provider": "Microsoft / Outlook",
     "text": "Throttled for reputation",
     "source": "Microsoft 365 error code reference"},
    {"code": "S3140", "provider": "Microsoft / Outlook",
     "text": "Internal reason code attached to an IP reputation block",
     "source": "Microsoft Outlook.com postmaster"},
    {"code": "S3150", "provider": "Microsoft / Outlook",
     "text": "Internal reason code attached to an IP reputation block",
     "source": "Microsoft Outlook.com postmaster"},
    {"code": "TS03", "provider": "Yahoo",
     "text": "Deferred for policy or reputation reasons",
     "source": "Yahoo postmaster error reference"},
    {"code": "TS04", "provider": "Yahoo",
     "text": "Deferred; connection rate or reputation",
     "source": "Yahoo postmaster error reference"},
]


def fetch():
    os.makedirs(SRC, exist_ok=True)
    for name, url in SOURCES.items():
        req = urllib.request.Request(url, headers={"User-Agent":
            "rastu.tech-registry/1.0 (+https://rastu.tech)"})
        with urllib.request.urlopen(req, timeout=60) as r:
            data = r.read()
        open(os.path.join(SRC, name), "wb").write(data)
        print(f"  fetched {name} ({len(data):,} bytes)")


def _md(text):
    """Flatten a Microsoft docs table cell into plain prose."""
    t = re.sub(r"\[([^\]]+)\]\([^)]*\)", r"\1", text)      # links to their label
    t = t.replace("<br>", " ").replace("`", "")
    t = re.sub(r"\*\*([^*]+)\*\*", r"\1", t)                 # bold to plain
    t = re.sub(r"Microsoft 365 or Office 365", "Microsoft 365", t)
    return re.sub(r"\s+", " ", t).strip()


def microsoft_codes():
    """Microsoft's NDR table, parsed from the cached documentation page.

    Codes appear singly and as ranges (5.7.606-649, 4.7.500-699). The range is
    kept intact rather than expanded: it is how Microsoft documents them, and
    expanding it would invent forty-three entries that say the same sentence.
    """
    path = os.path.join(SRC, MICROSOFT_SRC)
    if not os.path.exists(path):
        return []
    out = []
    for line in open(path, encoding="utf8", errors="replace").read().split("\n"):
        if not line.startswith("|"):
            continue
        cells = [c.strip() for c in line.strip().strip("|").split("|")]
        if len(cells) < 3:
            continue
        code = cells[0].strip("* `")
        if not re.match(r"^[45]\.\d\.\d{1,3}(-\d{1,3})?$", code):
            continue
        lo, _, hi = code.partition("-")
        out.append({
            "code": code,
            "low": lo,
            "high": lo.rsplit(".", 1)[0] + "." + hi if hi else lo,
            "provider": "Microsoft / Outlook",
            "sample": _md(cells[1]),
            "why": _md(cells[2]),
            "fix": _md(cells[3]) if len(cells) > 3 else "",
            "source": "Microsoft Learn, Exchange Online NDR reference",
            "url": MICROSOFT_URL,
        })
    return out


def basic_codes():
    """RFC 5321 section 4.2.3, parsed rather than retyped."""
    path = os.path.join(SRC, "rfc5321.txt")
    text = open(path, encoding="utf8", errors="replace").read()
    # Anchor on the section heading, not the first match: "4.2.3." appears in
    # the table of contents first and that block contains no codes at all.
    m = re.search(r"\n4\.2\.3\.\s+Reply Codes in Numeric Order", text)
    if not m:
        sys.exit("could not find RFC 5321 section 4.2.3 in the cached copy")
    end = text.index("4.2.4.", m.end())
    block = text[m.end():end]

    out, current = [], None
    for line in block.split("\n"):
        hit = re.match(r"^\s{3}([2-5]\d\d)\s\s+(\S.*)$", line)
        if hit:
            current = {"code": hit.group(1), "text": hit.group(2).strip()}
            out.append(current)
        elif current and re.match(r"^\s{5,}\S", line):
            current["text"] += " " + line.strip()
        elif not line.strip():
            continue
        else:
            current = None
    for c in out:
        # The RFC wraps explanatory parentheticals into the text; they read as
        # asides rather than as the meaning, so they are kept but tidied.
        c["text"] = re.sub(r"\s+", " ", c["text"]).strip().rstrip(".")
        c["class"] = c["code"][0]
    seen, uniq = set(), []
    for c in out:
        if c["code"] in seen:
            continue
        seen.add(c["code"])
        uniq.append(c)
    return uniq


def enhanced_codes():
    """The IANA registry, which is the authority for the enhanced codes."""
    path = os.path.join(SRC, "iana-esc.csv")
    out = []
    for r in csv.DictReader(open(path, encoding="utf8")):
        code = (r.get("Code") or "").strip()
        if not code.startswith("X."):
            continue
        out.append({
            "code": code,
            "sample": " ".join((r.get("Sample Text") or "").split()),
            "desc": " ".join((r.get("Description") or "").split()),
            "basic": " ".join((r.get("Associated basic status code") or "").split()),
            "ref": " ".join((r.get("Reference") or "").split()),
        })
    return out


def classes_for(entry):
    """Which of 2/4/5 a given X.n.n is actually seen with.

    IANA records a canonical basic status code for some entries, and using that
    alone would be wrong for a lookup tool: the registry pairs X.2.2 with 552,
    yet every real MTA emits 4.2.2 for a mailbox that is temporarily over quota.
    Somebody pasting "4.2.2" has to find it.

    So both failure classes are always offered, and success is offered only for
    the subjects where a success can meaningfully be reported. Where IANA does
    document a pairing it is kept alongside, so the canonical form is still
    identifiable.
    """
    subject = entry["code"].split(".")[1]
    # A success code is only meaningful where the subject can describe one:
    # there is no 2.4.x "routing succeeded" in practice.
    SUCCESS_SUBJECTS = {"0", "1", "2", "5", "6", "7"}
    out = ["4", "5"]
    if subject in SUCCESS_SUBJECTS and re.search(r"\b2\d\d\b|any", entry["basic"], re.I):
        out.insert(0, "2")
    return out


def action_for(cls, code):
    if code in OVERRIDE:
        act, note = OVERRIDE[code]
        return act, note, True
    subject = code.split(".")[1]
    key = (cls, None) if cls == "2" else (cls, subject)
    act, note = DERIVED_ACTION.get(key, ("review", "No default action for this class."))
    return act, note, False


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--fetch", action="store_true",
                    help="re-download the registries before building")
    a = ap.parse_args()
    if a.fetch:
        fetch()
    for name in SOURCES:
        if not os.path.exists(os.path.join(SRC, name)):
            sys.exit(f"{name} is not cached. Run with --fetch once.")

    basic = basic_codes()
    enhanced = enhanced_codes()

    expanded = []
    for e in enhanced:
        for cls in classes_for(e):
            code = cls + e["code"][1:]
            act, note, specific = action_for(cls, code)
            expanded.append({
                "code": code,
                "family": e["code"],
                "cls": cls,
                "subject": code.split(".")[1],
                "sample": e["sample"],
                "desc": e["desc"],
                "action": act,
                "note": note,
                "specific": specific,
                "canonical": bool(re.search(r"\b" + cls + r"\d\d\b", e["basic"])),
                "basic": e["basic"],
                "ref": e["ref"] or "RFC 3463",
            })

    microsoft = microsoft_codes()

    reg = {
        "basic": basic,
        "enhanced": sorted(expanded, key=lambda x: [int(n) for n in x["code"].split(".")]),
        "provider": PROVIDER,
        "microsoft": microsoft,
        "classes": CLASSES,
        "subjects": SUBJECTS,
        "sources": {
            "enhanced": {"name": "IANA SMTP Enhanced Status Codes registry",
                         "url": SOURCES["iana-esc.csv"]},
            "basic": {"name": "RFC 5321 section 4.2.3",
                      "url": "https://www.rfc-editor.org/rfc/rfc5321.html#section-4.2.3"},
            "microsoft": {"name": "Microsoft Learn, Exchange Online NDR reference",
                          "url": MICROSOFT_URL},
        },
    }
    json.dump(reg, open(OUT, "w", encoding="utf8"), indent=1, ensure_ascii=False)

    specific = sum(1 for x in reg["enhanced"] if x["specific"])
    print(f"  {len(reg['basic'])} basic reply codes (RFC 5321)")
    print(f"  {len(reg['enhanced'])} enhanced codes across classes "
          f"({len(enhanced)} families, {specific} with a written action)")
    print(f"  {len(reg['microsoft'])} Microsoft codes (many outside the IANA registry)")
    print(f"  {len(reg['provider'])} other provider-specific responses")
    total = len(reg["basic"]) + len(reg["enhanced"]) + len(reg["microsoft"]) \
        + len(reg["provider"])
    print(f"  {total} entries in total")
    print(f"  -> {OUT}")


if __name__ == "__main__":
    main()
