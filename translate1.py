#运行前先在cmd窗口安装
#pip install beautifulsoup4 requests chardet


import os
import zipfile
import bs4
import requests
import chardet
import shutil
import time
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from pathlib import Path
from threading import Thread

# ================== 自动创建临时文件夹 ==================
temp_folder = "temp_epub"
if not os.path.exists(temp_folder):
    os.makedirs(temp_folder)

# ================== 翻译核心（分段翻译 + Token统计）==================
def translate_single_chunk(text, api_url, model_name):
    if not text.strip():
        return text, 0, 0
    prompt = f"""你是专业英文翻译助手，将下面内容准确、流畅地翻译成中文。
只输出译文，不要解释、不要多余内容，保持段落格式。

英文：
{text}
"""
    payload = {
        "model": model_name,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.1,
        "max_tokens": 1024,
        "stream": False
    }
    try:
        r = requests.post(api_url, json=payload, timeout=120)
        r.raise_for_status()
        res = r.json()
        content = res["choices"][0]["message"]["content"].strip()

        prompt_tokens = res.get("usage", {}).get("prompt_tokens", 0)
        completion_tokens = res.get("usage", {}).get("completion_tokens", 0)
        return content, prompt_tokens, completion_tokens
    except Exception as e:
        return f"[翻译失败] {text}", 0, 0

def translate_with_retry_chunks(text, api_url, model_name, max_retry, app):
    if not text.strip():
        return text
    sentences = [s.strip() for s in text.splitlines() if s.strip()]
    translated = []
    total = len(sentences)
    for i, sent in enumerate(sentences):
        t = None
        p_tokens = 0
        c_tokens = 0
        for retry in range(max_retry):
            try:
                t, p_tokens, c_tokens = translate_single_chunk(sent, api_url, model_name)
                translated.append(t)
                app.update_single_progress(i+1, total)
                app.add_token_count(p_tokens, c_tokens)
                break
            except Exception as e:
                print(f"段落 {i+1}/{total} 重试 {retry+1}/{max_retry} 失败")
                time.sleep(2)
        time.sleep(0.5)
    return "\n".join(translated)

# ================== HTML 处理 ==================
def process_html(file_path, api_url, model_name, max_retry, app):
    with open(file_path, 'rb') as f:
        raw = f.read()
    enc = chardet.detect(raw)['encoding'] or 'utf-8'
    enc = enc.lower()
    if enc not in ['utf-8', 'gbk', 'gb2312']:
        enc = 'utf-8'

    try:
        with open(file_path, 'r', encoding=enc, errors='replace') as f:
            html = f.read()
    except:
        with open(file_path, 'r', encoding='utf-8', errors='replace') as f:
            html = f.read()

    soup = bs4.BeautifulSoup(html, 'html.parser')
    texts = list(soup.find_all(string=True))
    total_texts = len([t for t in texts if t.strip() and t.parent.name not in ['script','style','meta','link']])
    current = 0

    for t in texts:
        if not t.strip():
            continue
        if t.parent.name in ['script','style','meta','link']:
            continue
        current += 1
        original = t.strip()
        translated = translate_with_retry_chunks(original, api_url, model_name, max_retry, app)
        bilingual = f'''
<div style="margin:0.6em 0; padding:0.6em; border:1px solid #eee; border-radius:4px;">
<div style="color:#111;">{original}</div>
<div style="margin-top:0.4em; color:#0052cc; font-weight:500;">{translated}</div>
</div>'''
        new_tag = bs4.BeautifulSoup(bilingual, 'html.parser')
        t.replace_with(new_tag)
        app.update_total_progress(current, total_texts)

    with open(file_path, 'w', encoding='utf-8') as f:
        f.write(soup.prettify())

# ================== EPUB 处理 ==================
def process_epub(input_path, output_name, api_url, model_name, max_retry_str, app):
    try:
        max_retry = int(max_retry_str)
        app.reset_token_count()

        temp_dir = "temp_epub"
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
        os.makedirs(temp_dir, exist_ok=True)

        with zipfile.ZipFile(input_path, 'r') as zf:
            zf.extractall(temp_dir)

        html_files = []
        for root, _, files in os.walk(temp_dir):
            for f in files:
                if f.lower().endswith(('.html', '.xhtml', '.htm')):
                    html_files.append(Path(root)/f)

        app.init_total_progress(len(html_files))
        print(f"\n找到 {len(html_files)} 个文件待翻译")

        for i, fp in enumerate(html_files, 1):
            print(f"\n[{i}/{len(html_files)}] 处理: {fp.name}")
            app.set_file_progress(i, len(html_files))
            process_html(str(fp), api_url, model_name, max_retry, app)

        output_path = f"{output_name}.epub" if not output_name.endswith('.epub') else output_name
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zf:
            mimetype = Path(temp_dir)/"mimetype"
            if mimetype.exists():
                zf.write(mimetype, "mimetype")
            for root, _, files in os.walk(temp_dir):
                for f in files:
                    if f == "mimetype": continue
                    full = Path(root)/f
                    arc = full.relative_to(temp_dir)
                    zf.write(full, arc)

        shutil.rmtree(temp_dir)
        print(f"\n✅ 翻译完成！已保存：{output_path}")
        messagebox.showinfo("完成", f"翻译成功！\n{output_path}")

    except Exception as e:
        print(f"\n❌ 错误: {e}")
        messagebox.showerror("错误", str(e))
    finally:
        app.reset_ui()

# ================== GUI（带双进度条 + Token统计）==================
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EPUB 分段翻译工具 · LM Studio + Token统计")
        self.geometry("720x600")
        self.resizable(False, False)

        self.input_path = tk.StringVar()
        self.model_name = tk.StringVar(value="qwen/qwen3.5-9b")
        self.max_retry = tk.StringVar(value="10")
        self.output_name = tk.StringVar(value="output_bilingual")
        self.api_url = "http://localhost:5005/v1/chat/completions"

        # Token统计变量
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0

        frame = ttk.Frame(self, padding=20)
        frame.pack(fill=tk.BOTH, expand=True)

        ttk.Label(frame, text="选择 EPUB：").grid(row=0, column=0, sticky='w', pady=6)
        ttk.Entry(frame, textvariable=self.input_path, width=60).grid(row=0, column=1, padx=5)
        ttk.Button(frame, text="浏览", command=self.select_file).grid(row=0, column=2)

        ttk.Label(frame, text="模型名称：").grid(row=1, column=0, sticky='w', pady=6)
        ttk.Entry(frame, textvariable=self.model_name, width=60).grid(row=1, column=1, columnspan=2)

        ttk.Label(frame, text="重试次数：").grid(row=2, column=0, sticky='w', pady=6)
        ttk.Entry(frame, textvariable=self.max_retry, width=60).grid(row=2, column=1, columnspan=2)

        ttk.Label(frame, text="输出文件名：").grid(row=3, column=0, sticky='w', pady=6)
        ttk.Entry(frame, textvariable=self.output_name, width=60).grid(row=3, column=1, columnspan=2)

        # ================== Token 统计显示区域 ==================
        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=4, column=0, columnspan=3, sticky='ew', pady=10)

        ttk.Label(frame, text="📊 Token 使用统计", font=('微软雅黑',9,'bold')).grid(row=5, column=0, sticky='w', pady=4)
        self.token_label = ttk.Label(frame, text=(
            f"Prompt: {self.total_prompt_tokens} | Completion: {self.total_completion_tokens} | 总计: 0"
        ), font=('Consolas',9))
        self.token_label.grid(row=5, column=1, columnspan=2, sticky='w', pady=4)

        ttk.Separator(frame, orient=tk.HORIZONTAL).grid(row=6, column=0, columnspan=3, sticky='ew', pady=6)

        self.start_btn = ttk.Button(frame, text="开始翻译", command=self.start_translate, width=22)
        self.start_btn.grid(row=7, column=0, columnspan=3, pady=5)

        ttk.Label(frame, text="文件进度：").grid(row=8, column=0, sticky='w', pady=3)
        self.file_progress = ttk.Progressbar(frame, length=550, mode='determinate')
        self.file_progress.grid(row=8, column=1, columnspan=2, pady=3)

        ttk.Label(frame, text="段落进度：").grid(row=9, column=0, sticky='w', pady=3)
        self.single_progress = ttk.Progressbar(frame, length=550, mode='determinate')
        self.single_progress.grid(row=9, column=1, columnspan=2, pady=3)

        ttk.Label(frame, text="日志：").grid(row=10, column=0, sticky='w', pady=4)
        self.log_box = tk.Text(frame, height=14, width=88)
        self.log_box.grid(row=11, column=0, columnspan=3, sticky='w')
        self.log_box.config(state=tk.DISABLED)

        import sys
        sys.stdout = self

    def write(self, msg):
        self.log_box.config(state=tk.NORMAL)
        self.log_box.insert(tk.END, msg)
        self.log_box.see(tk.END)
        self.log_box.config(state=tk.DISABLED)

    def flush(self): pass

    def select_file(self):
        path = filedialog.askopenfilename(filetypes=[("EPUB", "*.epub")])
        if path: self.input_path.set(path)

    def start_translate(self):
        if not self.input_path.get():
            messagebox.showwarning("提示", "请选择EPUB")
            return
        self.start_btn.config(state=tk.DISABLED, text="翻译中...")
        Thread(target=self._run, daemon=True).start()

    def _run(self):
        process_epub(
            input_path=self.input_path.get(),
            output_name=self.output_name.get(),
            api_url=self.api_url,
            model_name=self.model_name.get(),
            max_retry_str=self.max_retry.get(),
            app=self
        )

    def init_total_progress(self, total):
        self.file_progress["maximum"] = total
        self.file_progress["value"] = 0

    def set_file_progress(self, current, total):
        self.file_progress["value"] = current
        self.update_idletasks()

    def update_single_progress(self, current, total):
        self.single_progress["maximum"] = total
        self.single_progress["value"] = current
        self.update_idletasks()

    def update_total_progress(self, c, t):
        self.update_idletasks()

    # ================== Token 统计方法 ==================
    def reset_token_count(self):
        self.total_prompt_tokens = 0
        self.total_completion_tokens = 0
        self.token_label.config(text=f"Prompt: 0 | Completion: 0 | 总计: 0")

    def add_token_count(self, prompt, completion):
        self.total_prompt_tokens += prompt
        self.total_completion_tokens += completion
        total = self.total_prompt_tokens + self.total_completion_tokens
        self.token_label.config(text=(
            f"Prompt: {self.total_prompt_tokens} | Completion: {self.total_completion_tokens} | 总计: {total}"
        ))
        self.update_idletasks()

    def reset_ui(self):
        self.start_btn.config(state=tk.NORMAL, text="开始翻译")
        self.file_progress["value"] = 0
        self.single_progress["value"] = 0

# ================== 启动 ==================
if __name__ == "__main__":
    app = App()
    app.mainloop()