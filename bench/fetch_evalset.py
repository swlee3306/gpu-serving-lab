#!/usr/bin/env python3
"""Download the multiple-choice evaluation sets used by eval_mc.py and write them next to this file.

The questions are NOT stored in this repository. They come from two public datasets, each under
its own license, and are fetched from the Hugging Face Hub when you run this script:

  KMMLU  (Korean)   HAERAE-HUB/KMMLU   CC BY-ND 4.0   file data/Computer-Science-test.csv, first 600 rows
  MMLU   (English)  cais/mmlu          MIT            test split of six subjects (see SUBJECTS)

Output (same format and ids as the sets used for the measurements in docs/):
  evalset.json      200 questions: KMMLU Computer-Science rows 0-99 (ids k0..k99) + MMLU machine_learning
                    test questions 0-99 (ids m0..m99)
  evalset_big.json  1,257 questions: KMMLU Computer-Science rows 0-599 (ids k0..k599) + MMLU test
                    questions of six subjects (ids m_<subject>_<n>)

Each question is a dict {"id", "lang", "q", "a"}: q holds the question and the four options
("A. ..." to "D. ...") separated by newlines, a is the correct letter.

The script writes the files verbatim from the source data (no editing, no filtering), then compares the
SHA-256 of what it wrote with the hashes recorded when the measurements were made. A mismatch means the
upstream data changed since then; the files are still written and the script exits with status 3.

Standard library only. Needs network access to huggingface.co and datasets-server.huggingface.co.

Usage:  python3 bench/fetch_evalset.py [--out-dir DIR]
Credit: KMMLU, Son et al. 2024 (HAE-RAE). MMLU, Hendrycks et al. 2021. Please cite and follow their licenses.
"""
import argparse
import csv
import hashlib
import io
import json
import os
import sys
import time
import urllib.parse
import urllib.request

KMMLU_URL = "https://huggingface.co/datasets/HAERAE-HUB/KMMLU/resolve/main/data/Computer-Science-test.csv"
MMLU_ROWS = "https://datasets-server.huggingface.co/rows"
# order matters: it is the order of questions in evalset_big.json
SUBJECTS = ["machine_learning", "computer_security", "college_computer_science",
            "high_school_computer_science", "electrical_engineering", "college_mathematics"]
KO_BIG, KO_SMALL, EN_SMALL = 600, 100, 100
# SHA-256 of the two files as used for the measurements (see docs/step-1c-optimization.md)
EXPECTED = {
    "evalset.json": "fabdfc381aca9153475ff86d2f260a9328e5209ce740a06e3383f898bea1d799",
    "evalset_big.json": "e7a16e626bc465d8852de7fe1d3812f23de06384694f5e95875ffb66a2f8c556",
}


def get(url, tries=4):
    last = None
    for i in range(tries):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers={"User-Agent": "gpu-serving-lab/fetch_evalset"}), timeout=60) as r:
                return r.read()
        except Exception as e:  # network hiccups and 429s: retry with backoff
            last = e
            time.sleep(1.5 * (i + 1))
    sys.exit(f"download failed: {url}: {last}")


def fmt(question, options):
    return question + "".join(f"\n{l}. {o}" for l, o in zip("ABCD", options))


def kmmlu():
    text = get(KMMLU_URL).decode("utf-8")
    rows = list(csv.DictReader(io.StringIO(text)))
    if len(rows) < KO_BIG:
        sys.exit(f"KMMLU file has {len(rows)} rows, expected at least {KO_BIG}")
    out = []
    for i, r in enumerate(rows[:KO_BIG]):
        out.append({"id": f"k{i}", "lang": "ko", "q": fmt(r["question"], [r["A"], r["B"], r["C"], r["D"]]),
                    "a": "ABCD"[int(r["answer"]) - 1]})
    return out


def mmlu(subject):
    out, offset = [], 0
    while True:
        qs = urllib.parse.urlencode({"dataset": "cais/mmlu", "config": subject, "split": "test", "offset": offset, "length": 100})
        d = json.loads(get(f"{MMLU_ROWS}?{qs}"))
        rows = d["rows"]
        for r in rows:
            row = r["row"]
            out.append((fmt(row["question"], row["choices"]), "ABCD"[row["answer"]]))
        offset += len(rows)
        if not rows or offset >= d["num_rows_total"]:
            return out


def dump(items, path):
    data = json.dumps(items, ensure_ascii=False).encode("utf-8")
    with open(path, "wb") as f:
        f.write(data)
    return hashlib.sha256(data).hexdigest()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out-dir", default=os.path.dirname(os.path.abspath(__file__)))
    out_dir = ap.parse_args().out_dir
    os.makedirs(out_dir, exist_ok=True)

    ko = kmmlu()
    en = {s: mmlu(s) for s in SUBJECTS}
    big = list(ko)
    for s in SUBJECTS:
        big += [{"id": f"m_{s}_{i}", "lang": "en", "q": q, "a": a} for i, (q, a) in enumerate(en[s])]
    small = ko[:KO_SMALL] + [{"id": f"m{i}", "lang": "en", "q": q, "a": a}
                             for i, (q, a) in enumerate(en["machine_learning"][:EN_SMALL])]

    got = {"evalset.json": dump(small, os.path.join(out_dir, "evalset.json")),
           "evalset_big.json": dump(big, os.path.join(out_dir, "evalset_big.json"))}
    print(f"wrote {len(small)} and {len(big)} questions to {out_dir}")
    bad = [n for n in got if got[n] != EXPECTED[n]]
    for n in got:
        print(f"  {n}: sha256 {got[n][:16]}... {'matches the measured set' if n not in bad else 'DIFFERS from the measured set'}")
    if bad:
        print("The upstream data changed since the measurements; results may not be comparable.", file=sys.stderr)
        sys.exit(3)


if __name__ == "__main__":
    main()
