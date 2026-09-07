"""Simple newline-delimited on-disk queue used to drive data-driven ``# LOOP`` bodies.

Subcommands (each takes a ``<file>`` positional path):

  init <file>   Read newline-delimited items from stdin, dedupe adjacent
                blanks/whitespace, write to ``<file>``. Existing file is
                replaced. Also accepts ``--items name --items name`` on the
                CLI as an alternate input path.

  peek <file>   Print the first non-blank line on stdout (no newline stripping
                of internal whitespace).
                Exit 0 iff the queue has at least one item, 1 if the queue is
                empty or missing. Useful as a ``# LOOP`` condition.

  pop <file>    Remove the first line from ``<file>``. Exit 0 if a line was
                removed, exit 1 if the queue was already empty.

  count <file>  Print the count of remaining items on stdout. Always exit 0.

Blank lines are ignored on read.
"""
import argparse, os, sys


def _read(path):
    if not os.path.isfile(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        return [ln.rstrip("\r\n") for ln in f if ln.strip()]


def _write(path, items):
    os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        for it in items:
            f.write(it + "\n")


def cmd_init(a):
    if a.items:
        items = [x.strip() for x in a.items if x.strip()]
    else:
        items = [ln.rstrip("\r\n") for ln in sys.stdin if ln.strip()]
    _write(a.file, items)
    print(f"wrote {len(items)} item(s) to {a.file}")


def cmd_peek(a):
    items = _read(a.file)
    if not items:
        print("queue empty", file=sys.stderr)
        sys.exit(1)
    print(items[0])


def cmd_pop(a):
    items = _read(a.file)
    if not items:
        print("queue empty", file=sys.stderr)
        sys.exit(1)
    dropped = items.pop(0)
    _write(a.file, items)
    print(f"popped: {dropped!r}; {len(items)} remaining")


def cmd_count(a):
    items = _read(a.file)
    print(len(items))


def main():
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="mode", required=True)

    sp = sub.add_parser("init"); sp.add_argument("file")
    sp.add_argument("--items", action="append", default=None,
                    help="explicit item; may be given multiple times (instead of reading stdin)")
    sp.set_defaults(fn=cmd_init)

    for name, fn in (("peek", cmd_peek), ("pop", cmd_pop), ("count", cmd_count)):
        sp = sub.add_parser(name); sp.add_argument("file"); sp.set_defaults(fn=fn)

    a = p.parse_args()
    a.fn(a)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:
        print(f"ERROR: {e}", file=sys.stderr); sys.exit(2)
