"""生成「手机扫码打开」页面：自动取本机局域网 IP，输出二维码 + 使用说明。

    .venv/Scripts/python.exe scripts/make_phone_qr.py --port 8765

电脑换 Wi-Fi / 重启路由导致 IP 变化时，重新跑一次即可。
依赖 qrcode（仅本工具用，主程序不依赖）：.venv/Scripts/python.exe -m pip install qrcode
"""
from __future__ import annotations

import argparse
import base64
import html
import io
import socket
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = ROOT / "report"


def lan_ip() -> str:
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("223.5.5.5", 80))  # 不发包，只让系统选默认出口网卡
            return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"


def qr_data_url(url: str) -> str | None:
    try:
        import qrcode
    except ImportError:
        return None
    qr = qrcode.QRCode(error_correction=qrcode.constants.ERROR_CORRECT_M,
                       box_size=12, border=3)
    qr.add_data(url)
    qr.make(fit=True)
    img = qr.make_image(fill_color=(28, 32, 38), back_color="white").convert("RGB")
    img.save(OUT_DIR / "phone_qr.png")
    buf = io.BytesIO()
    img.save(buf, "PNG")
    return "data:image/png;base64," + base64.b64encode(buf.getvalue()).decode()


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args()
    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ip = lan_ip()
    url = f"http://{ip}:{args.port}/"
    u = html.escape(url, quote=True)
    q = qr_data_url(url)
    qr_block = (f'<img src="{q}" alt="手机访问二维码">' if q else
                '<p style="color:#b43636">未安装 qrcode，请直接复制下面的地址；'
                '或执行 <code>pip install qrcode</code> 后重跑本脚本。</p>')
    page = f'''<!doctype html><html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>手机上打开 · 饮料换装</title><style>
:root{{color-scheme:light}}*{{box-sizing:border-box}}
body{{margin:0;background:#f5f7fa;color:#1c2026;font:15px/1.7 -apple-system,"Segoe UI","Microsoft YaHei",sans-serif}}
.wrap{{max-width:660px;margin:0 auto;padding:32px 20px 60px}}
h1{{font-size:23px;margin:0 0 6px}}.lead{{color:#5b6472;margin:0 0 20px}}
.card{{background:#fff;border:1px solid #e3e6eb;border-radius:12px;padding:22px;text-align:center;margin-bottom:18px}}
.card img{{width:260px;max-width:100%;height:auto;display:block;margin:0 auto 16px}}
.addr{{display:inline-block;background:#eef3fa;color:#12459b;font:600 20px/1.4 ui-monospace,Consolas,monospace;
 padding:10px 16px;border-radius:8px;word-break:break-all}}
a.link{{color:#12459b}} ol{{padding-left:22px}} li{{margin:9px 0}}
.note{{background:#fff;border:1px solid #e3e6eb;border-left:3px solid #d64747;border-radius:8px;padding:12px 16px;color:#43506a;font-size:13px}}
code{{background:#eef1f6;padding:1px 5px;border-radius:4px;font-size:12px}}
</style></head><body><div class="wrap">
<h1>在手机上打开「饮料换装」</h1>
<p class="lead">电脑上的服务已经开放到局域网，用手机扫下面的二维码，或在手机浏览器直接输入地址。</p>
<div class="card">
  {qr_block}
  <div class="addr">{u}</div>
  <p style="color:#5b6472;font-size:13px;margin:14px 0 0">也可直接点这个链接：<a class="link" href="{u}">{u}</a></p>
</div>
<ol>
  <li><b>手机和电脑连同一个 Wi-Fi</b>（电脑当前 Wi-Fi 地址 <code>{html.escape(ip)}</code>）。</li>
  <li>用手机<b>系统浏览器</b>（Safari / Chrome）打开上面的地址；出现「不安全」提示时选「继续访问」，这是本机 HTTP 服务，不涉及外网。</li>
  <li>点虚线框上传/拍照 → 选一张人像 → 点「开始匹配」，也可以先搜索并指定饮料。大图会在手机里自动压缩后再上传。</li>
  <li>生成的合成图可以直接<b>长按保存</b>到相册。</li>
</ol>
<div class="note">
<b>打不开时依次检查：</b><br>
① 手机是否和电脑在同一 Wi-Fi（校园网/公司网常开「AP 隔离」，会禁止设备互访，这时改用电脑开个人热点让手机连）；<br>
② 电脑是否休眠、服务是否被关掉（本机 <code>http://127.0.0.1:{args.port}/</code> 能打开说明服务正常）；<br>
③ 电脑 IP 变了（重启路由/换网络后 DHCP 会换地址），重新运行 <code>scripts/make_phone_qr.py</code> 会生成新地址的二维码；<br>
④ 照片只在这台电脑的内存/本地报告里处理，<b>不会上传到公网</b>；链接仅同一局域网可访问，用完可随时关掉服务。
</div>
</div></body></html>'''
    (OUT_DIR / "phone_access.html").write_text(page, encoding="utf-8")
    print(f"手机地址：{url}")
    print(f"已生成 {OUT_DIR / 'phone_access.html'}")


if __name__ == "__main__":
    main()
