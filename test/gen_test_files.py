#!/usr/bin/env python3
"""批量生成用于测试的文件。

生成的文件名混合了多种模式，模拟真实场景：
- 字母前缀（ABC-123、ABC_123）
- FC2（FC2-1234567、FC2PPV-1234567）
- 6 位数字前缀（123456-789）
- 不匹配的噪声文件名

用法（在项目根目录执行）：
    # 默认：10000 个文件，生成到 test/files/
    python test/gen_test_files.py

    # 10 万个
    python test/gen_test_files.py --count 100000

    # 指定目录
    python test/gen_test_files.py --count 5000 --dir test/files_big

    # 每个文件写 1KB 内容（测试磁盘占用时用）
    python test/gen_test_files.py --count 10000 --size 1024

    # 清理
    python test/gen_test_files.py --clean
"""

from __future__ import annotations

import argparse
import random
import shutil
import string
import sys
from pathlib import Path

# 从 defaults.py 里抓一部分真实前缀，避免手抄
PREFIXES = [
    "ABP", "ADN", "AKHO", "ARA", "ARM", "AVOP", "BBTU", "CAWD", "CJOD",
    "CKK", "CLT", "CMA", "CMF", "COFD", "CRC", "DASD", "DDT", "DPMI",
    "DSVR", "DVAJ", "DVDES", "DVDMS", "DWD", "DWI", "EBOD", "EKDV",
    "ABS", "FAX", "FSET", "GACHIP", "GAS", "GMEN", "GUN", "HEYZO",
    "HIKR", "HJMO", "HMGL", "HMM", "HND", "HONB", "HTMS", "HUNT",
    "HUNTA", "IENE", "IPX", "IPZZ", "JAC", "JUFE", "JUL", "JUQ", "JUR",
    "JUX", "JUY", "JYMA", "KAWD", "KBI", "KING", "KNB", "KSBJ", "KYW",
    "LAFBD", "LUXU", "MAD", "MADM", "MCB3DBD", "MCS", "MDBK", "MEKO",
    "MESS", "MEYD", "MIAA", "MIAD", "MIDE", "MIDV", "MIGD", "MIMK",
    "MIRD", "MIST", "MIZD", "MKBD", "MKCK", "MKD", "MVSD", "MXGS",
    "NATR", "NCYF", "NDS", "NEO", "NHDB", "NHDTA", "NHDTB", "NHDTC",
    "NITR", "NNPJ", "NSPS", "NYB", "OBA", "OFJE", "OHO", "OKSN", "OOMN",
    "ORECS", "PGD", "PGFS", "PPBD", "PPPD", "PSI", "PTS", "RBD", "RCT",
    "RCTD", "RED", "RKI", "RMER", "SAMA", "SAME", "SCNN", "SCOP",
    "SDAM", "SDDE", "SDMF", "SDMM", "SDMS", "SDMT", "SDMU", "SDNM",
    "SDNT", "SERO", "SHKD", "SIM", "SIRO", "SIVR", "SKMJ", "SKYHD",
    "SMBD", "SMD", "SNIS", "SOE", "SONE", "SPRD", "SRS", "SSIS",
    "SSNI", "STAR", "STARS", "START", "SUPD", "SVDVD", "SVVRT", "SYK",
    "TEN", "TMVI", "TUE", "TWYB", "TYWD", "UMSO", "URE", "USAG", "VAGU",
    "VDD", "VENU", "VENX", "VOD", "WAAA", "MISM", "WANZ", "WBW", "WCX",
    "YMDD", "NWF", "DDB", "SDJS", "CRPD", "CLUB", "MDYD", "BOMN",
]

VIDEO_EXTS = [".mp4", ".mkv", ".avi", ".wmv", ".mov", ".ts", ".iso"]
SUB_EXTS = [".srt", ".ass"]

# 脚本所在目录（test/），默认输出到 test/files
SCRIPT_DIR = Path(__file__).resolve().parent
DEFAULT_OUTPUT = SCRIPT_DIR / "files"


def _make_filename(rng: random.Random) -> str:
    """生成一个文件名。70% 命中前缀，10% FC2，10% 数字前缀，10% 噪声。"""
    kind = rng.choices(
        ["alpha", "fc2", "digital", "noise"],
        weights=[70, 10, 10, 10],
        k=1,
    )[0]

    sep = rng.choice(["-", "_"])
    ext = rng.choice(VIDEO_EXTS + SUB_EXTS)

    if kind == "alpha":
        prefix = rng.choice(PREFIXES)
        n = rng.randint(10, 9999)
        stem = f"{prefix}{sep}{n:03d}"
    elif kind == "fc2":
        n = rng.randint(100000, 9999999)
        if rng.random() < 0.4:
            stem = f"FC2{sep}PPV{sep}{n}"
        else:
            stem = f"FC2{sep}{n}"
    elif kind == "digital":
        a = rng.randint(100000, 999999)
        b = rng.randint(100, 999)
        stem = f"{a}{sep}{b}"
    else:
        stem = "".join(
            rng.choices(string.ascii_letters + " _-", k=rng.randint(6, 20))
        ).strip()

    # 40% 概率加一个尾巴，模拟 "ABC-123 某某标题.mp4"
    tail = ""
    if rng.random() < 0.4:
        tail = " " + "".join(
            rng.choices(string.ascii_letters + string.digits,
                        k=rng.randint(3, 12))
        )

    return f"{stem}{tail}{ext}"


def generate(root: Path, count: int, per_dir: int,
             size: int = 0, seed: int = 0) -> int:
    """生成 count 个文件，每 per_dir 个放到一个子目录。返回实际生成数。"""
    rng = random.Random(seed)
    root.mkdir(parents=True, exist_ok=True)

    total = 0
    dir_idx = 0
    payload = b"\0" * size if size > 0 else b""

    while total < count:
        sub = root / f"group_{dir_idx:04d}"
        sub.mkdir(exist_ok=True)
        dir_idx += 1

        n_in_dir = min(per_dir, count - total)
        seen: set[str] = set()
        for _ in range(n_in_dir):
            name = _make_filename(rng)
            for _ in range(5):
                if name not in seen:
                    break
                name = _make_filename(rng)
            seen.add(name)

            p = sub / name
            if size > 0:
                with open(p, "wb") as f:
                    f.write(payload)
            else:
                p.touch()
            total += 1

        if total % 5000 == 0 or total == count:
            print(f"  已生成 {total}/{count}")

    return total


def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量生成测试文件",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument("--dir", default=str(DEFAULT_OUTPUT),
                        help=f"输出目录（默认 {DEFAULT_OUTPUT}）")
    parser.add_argument("--count", type=int, default=10000,
                        help="要生成的文件数（默认 10000）")
    parser.add_argument("--per-dir", type=int, default=500,
                        help="每个子目录最多放多少个文件（默认 500）")
    parser.add_argument("--size", type=int, default=0,
                        help="每个文件的字节数，0 表示空文件（默认 0）")
    parser.add_argument("--seed", type=int, default=0,
                        help="随机种子（默认 0）")
    parser.add_argument("--clean", action="store_true",
                        help="删除目标目录后退出，不生成文件")
    args = parser.parse_args()

    root = Path(args.dir).expanduser().resolve()

    if args.clean:
        if not root.exists():
            print(f"{root} 不存在")
            return
        confirm = input(f"删除 {root} 及其中所有文件？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消")
            return
        shutil.rmtree(root)
        print(f"已删除 {root}")
        return

    if root.exists() and any(root.iterdir()):
        print(f"警告：{root} 已存在且非空。")
        confirm = input("将追加文件到已有目录，继续？(y/N): ").strip().lower()
        if confirm != "y":
            print("已取消")
            return

    print(f"生成 {args.count} 个文件到：{root}")
    n = generate(
        root,
        count=args.count,
        per_dir=args.per_dir,
        size=args.size,
        seed=args.seed,
    )
    dir_count = (n + args.per_dir - 1) // args.per_dir
    print(f"完成：{n} 个文件，{dir_count} 个子目录")
    if args.size > 0:
        total_mb = n * args.size / 1024 / 1024
        print(f"预计占用约 {total_mb:.1f} MB")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n中断")
        sys.exit(1)