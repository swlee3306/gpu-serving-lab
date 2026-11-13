import json, os, re, subprocess, tempfile, time, urllib.request


def chat(base_url, model, prompt, temperature=0.0, max_tokens=1500, think=False, timeout=300):
    body = {"model": model, "messages": [{"role": "user", "content": prompt}], "temperature": temperature, "max_tokens": max_tokens}
    if not think:
        body["chat_template_kwargs"] = {"enable_thinking": False}
    req = urllib.request.Request(base_url.rstrip("/") + "/v1/chat/completions", json.dumps(body).encode(), {"Content-Type": "application/json"})
    t = time.time()
    r = json.load(urllib.request.urlopen(req, timeout=timeout))
    return r["choices"][0]["message"]["content"] or "", time.time() - t, r["usage"]["completion_tokens"]


def extract_json(text):
    """응답에서 첫 번째 JSON 값(객체 또는 배열)을 꺼낸다. 실패하면 None."""
    t = text.strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    starts = [(t.find(o), o, c) for o, c in (("{", "}"), ("[", "]")) if t.find(o) >= 0]
    for i, opener, closer in sorted(starts):   # 먼저 나오는 여는 괄호부터 시도한다(배열 안의 첫 객체를 꺼내지 않도록)
        depth = 0; in_str = False; esc = False
        for j in range(i, len(t)):
            ch = t[j]
            if in_str:
                if esc: esc = False
                elif ch == "\\": esc = True
                elif ch == '"': in_str = False
                continue
            if ch == '"': in_str = True
            elif ch == opener: depth += 1
            elif ch == closer:
                depth -= 1
                if depth == 0:
                    try: return json.loads(t[i:j + 1])
                    except Exception: return None
    return None


def chat_cli(kind, model, prompt, timeout=600):
    """구독 CLI(claude/codex)를 비대화식으로 호출한다. 온도 지정 불가(기본값). 빈 임시 디렉터리에서 실행해 프로젝트 문맥이 섞이지 않게 한다."""
    t = time.time()
    with tempfile.TemporaryDirectory() as d:
        if kind == "claude":
            cmd = ["claude", "-p", "--tools", "", "--no-session-persistence", "--disable-slash-commands", "--output-format", "text"]
            if model: cmd += ["--model", model]
            r = subprocess.run(cmd + [prompt], cwd=d, capture_output=True, text=True, timeout=timeout)
            out = r.stdout
        else:
            of = os.path.join(d, "out.txt")
            cmd = ["codex", "exec", "--sandbox", "read-only", "--skip-git-repo-check", "--ephemeral", "-C", d, "-o", of]
            if model: cmd += ["-m", model]
            subprocess.run(cmd + ["도구 사용이나 되묻기 없이, 아래 요청에 대한 답만 출력해.\n\n" + prompt], capture_output=True, text=True, timeout=timeout)
            out = open(of, encoding="utf-8").read() if os.path.exists(of) else ""
    return out, time.time() - t, 0
