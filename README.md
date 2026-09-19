# smtp-responses

Every SMTP response an operator is likely to meet, as one machine-readable set.
**272 entries** parsed from the authoritative sources, each carrying what it
means, where it came from, and what to do about it.

Live search over this data: **[rastu.tech/smtp](https://rastu.tech/smtp/)**

## Why this exists

The definitions are already published, in three places that do not talk to each
other and in three different formats. What none of them carry is the thing you
actually want at three in the morning: whether to retry, suppress, slow down, or
stop and fix something. So every entry here also has an action.

Two details no other collection handles:

- **Microsoft documents ranges.** `5.7.606-649` is one row in their reference and
  covers forty-four distinct codes. A lookup for `5.7.620` has to resolve to it,
  and here it does.
- **A code is not a port.** `587` and `465` look exactly like 5xx codes. The
  parsing side of this ships a closed set of the reply codes RFC 5321 actually
  defines, so a pasted log yields responses rather than the port number and the
  first octet of an IP address.

## Sources

| Source | What it gives | Count |
|---|---|---|
| [RFC 5321 section 4.2.3](https://www.rfc-editor.org/rfc/rfc5321.html#section-4.2.3) | the reply codes the standard defines | 24 |
| [IANA SMTP Enhanced Status Codes](https://www.iana.org/assignments/smtp-enhanced-status-codes/) | every registered enhanced status code, across the classes it applies to | 168 |
| [Microsoft NDR reference](https://learn.microsoft.com/en-us/exchange/mail-flow-best-practices/non-delivery-reports-in-exchange-online/non-delivery-reports-in-exchange-online) | Exchange Online codes, most of which are in neither registry | 69 |
| in common use | responses several providers emit that no registry defines | 11 |

Nothing is typed in by hand. The sources are cached under `sources/` so a build
is reproducible offline and every line can be traced back.

## The data

```json
{
  "enhanced": [
    {
      "code": "5.7.1",
      "cls": "5",
      "subject": "7",
      "sample": "Delivery not authorized, message refused",
      "desc":   "...",
      "action": "review",
      "specific": true,
      "basic": ["550", "551"],
      "ref": "RFC 3463"
    }
  ],
  "basic": [ ... ], "microsoft": [ ... ], "provider": [ ... ]
}
```

`action` is one of `deliver`, `retry`, `throttle`, `review`, `fix_config`,
`suppress` or `pause`.

`specific` is the honest bit. `true` means the action was written for that code.
`false` means it was derived from the code's class and subject, which is a sound
default and is not the same thing as somebody having worked that failure. A
reference that lets those two read alike is not worth trusting, so this one keeps
them apart.

## Use it

```bash
python3 export_registry.py            # rebuild from the cached sources
python3 export_registry.py --fetch    # re-download them first
```

```python
import json
reg = json.load(open("registry.json"))
by_code = {e["code"]: e for e in reg["enhanced"]}
print(by_code["5.7.1"]["action"])     # review
```

Or fetch it directly: <https://rastu.tech/registry.json>

## Licence

Code MIT. The data is CC BY 4.0: reuse it, and a citation is appreciated.

Maintained by [Rastu Singh](https://rastu.tech/about/), who runs email
infrastructure for a living.
