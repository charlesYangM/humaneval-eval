#!/usr/bin/env python3
import requests, json, os, time

# 读 DeepSeek API Key
api_key = ""
for path in [os.path.expanduser("~/.hermes/.env"), os.path.expanduser("~/.bashrc")]:
    with open(path) as f:
        for line in f:
            line = line.strip()
            if "DEEPSEEK_API_KEY" in line and "=" in line and not line.startswith("#"):
                api_key = line.split("=", 1)[1].strip().strip('"').strip("'")
                break
    if api_key:
        break

print(f"Key length: {len(api_key)}")

url = "https://api.deepseek.com/chat/completions"

def ask_deepseek(prompt, model="deepseek-v4-pro"):
    headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
    data = {
        "model": model,
        "messages": [{"role": "user", "content": prompt}],
        "max_tokens": 2000
    }
    start = time.time()
    resp = requests.post(url, headers=headers, json=data, timeout=120)
    dur = time.time() - start
    if resp.status_code == 200:
        r = resp.json()
        content = r['choices'][0]['message']['content']
        usage = r.get('usage', {})
        print(f"[{dur:.1f}s | in={usage.get('prompt_tokens')} out={usage.get('completion_tokens')}]")
        return content
    else:
        print(f"Error [{resp.status_code}]: {resp.text[:300]}")
        return None

# 1. flask-5014
print("\n=== pallets__flask-5014 ===")
prompt1 = """Read this SWE-bench issue and generate a git patch to fix it.

Issue: Require a non-empty name for Blueprints. Things do not work correctly if a Blueprint is given an empty name. It would be helpful if a ValueError was raised when trying to do that.

The Blueprint class is in `src/flask/blueprints.py`. The __init__ method currently checks:
```python
if "." in name:
    raise ValueError("'name' may not contain a dot '.' character.")
self.name = name
```

Generate a unified diff patch (git diff format) that adds a check for empty name after the existing dot check. Output ONLY the patch, no explanation."""

patch1 = ask_deepseek(prompt1)
print(patch1[:200] if patch1 else "FAILED")

# 2. requests-1142
print("\n=== psf__requests-1142 ===")
prompt2 = """Read this SWE-bench issue and generate a git patch to fix it.

Issue: requests.get is ALWAYS sending content-length header. The right behavior is not to add this header automatically in GET requests.

The relevant code is in `requests/models.py`:
```python
def prepare_content_length(self, body):
    self.headers['Content-Length'] = '0'
    if hasattr(body, 'seek') and hasattr(body, 'tell'):
        body.seek(0, 2)
        self.headers['Content-Length'] = str(body.tell())
        body.seek(0, 0)
    elif body is not None:
        self.headers['Content-Length'] = str(len(body))
```

Generate a unified diff patch (git diff format) that only sets Content-Length when there's actually a body AND the method is not GET/HEAD. Output ONLY the patch, no explanation."""

patch2 = ask_deepseek(prompt2)
print(patch2[:200] if patch2 else "FAILED")

# 保存 predictions
if patch1 and patch2:
    workdir = os.environ.get("RESULTS_DIR", os.path.expanduser("~/swebench-test/results/hermes-swe-20260621_deepseek-v4-pro"))
    os.makedirs(workdir, exist_ok=True)
    preds = [
        {"instance_id": "pallets__flask-5014", "model_patch": patch1, "model_name_or_path": "deepseek-v4-pro"},
        {"instance_id": "psf__requests-1142", "model_patch": patch2, "model_name_or_path": "deepseek-v4-pro"},
    ]
    with open(f"{workdir}/predictions.jsonl", "w") as f:
        for p in preds:
            f.write(json.dumps(p) + "\n")
    print(f"\nSaved predictions to {workdir}/predictions.jsonl")
