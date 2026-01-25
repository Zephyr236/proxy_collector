#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
极简代理收集器
仅实现爬取、验证、保存三个核心功能
"""
import sys
import os
import json
import time
import requests
import io
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional
from datetime import datetime

# 设置标准输出编码为UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.upper() != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.upper() != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 简单配置
CONFIG = {
    "crawler_workers": 2,
    "validator_workers": 10,
    "timeout": 15,
    "test_url": "https://httpbin.org/ip",
    "max_response_time": 5.0,
    "data_dir": "./data",
    "data_file": "proxies.json"
}

def setup_data_dir():
    """设置数据目录"""
    os.makedirs(CONFIG["data_dir"], exist_ok=True)

def load_existing_proxies() -> List[str]:
    """加载已有的代理"""
    data_file = os.path.join(CONFIG["data_dir"], CONFIG["data_file"])
    if os.path.exists(data_file):
        try:
            with open(data_file, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return data.get("proxies", [])
        except:
            pass
    return []

def save_proxies(proxies: List[str]):
    """保存代理列表"""
    data_file = os.path.join(CONFIG["data_dir"], CONFIG["data_file"])
    data = {
        "version": "1.0",
        "last_updated": datetime.utcnow().isoformat() + "Z",
        "total_proxies": len(proxies),
        "proxies": proxies
    }

    with open(data_file, 'w', encoding='utf-8') as f:
        json.dump(data, f, indent=2, ensure_ascii=False)

    return True

def fetch_geonode_proxies() -> List[str]:
    """从Geonode获取代理"""
    proxies = []
    url = "https://proxylist.geonode.com/api/proxy-list?limit=500&page=1&sort_by=lastChecked&sort_type=desc"

    # 增加重试机制
    max_retries = 2
    for attempt in range(max_retries):
        try:
            timeout = CONFIG["timeout"] * (attempt + 1)  # 每次重试增加超时时间
            response = requests.get(url, timeout=timeout)
            if response.status_code == 200:
                data = response.json()
                for item in data.get("data", []):
                    ip = item.get("ip")
                    port = item.get("port")
                    protocols = item.get("protocols", [])

                    if ip and port and protocols:
                        protocol = protocols[0] if protocols else "http"
                        proxy = f"{protocol}://{ip}:{port}"
                        proxies.append(proxy)
                break  # 成功则退出重试循环
            else:
                print(f"Geonode请求失败 (HTTP {response.status_code})，重试 {attempt+1}/{max_retries}")
        except Exception as e:
            if attempt == max_retries - 1:
                print(f"Geonode爬取失败: {e}")
            else:
                print(f"Geonode爬取失败，重试 {attempt+1}/{max_retries}: {e}")
                time.sleep(2)  # 重试前等待

    return proxies

def fetch_free_proxy_list() -> List[str]:
    """从free-proxy-list.net获取代理"""
    proxies = []
    url = "https://free-proxy-list.net/"

    try:
        response = requests.get(url, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            # 简单解析表格
            import re
            # 查找IP:Port格式
            pattern = r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d{2,5})'
            matches = re.findall(pattern, response.text)
            for ip, port in matches:
                proxy = f"http://{ip}:{port}"
                proxies.append(proxy)
    except Exception as e:
        print(f"Free Proxy List爬取失败: {e}")

    return proxies

def crawl_proxies() -> List[str]:
    """爬取所有代理源"""
    print("开始爬取代理...")

    all_proxies = []

    # 从多个源爬取
    sources = [
        fetch_geonode_proxies,
        fetch_free_proxy_list,
    ]

    with ThreadPoolExecutor(max_workers=CONFIG["crawler_workers"]) as executor:
        futures = [executor.submit(source) for source in sources]

        for future in as_completed(futures):
            try:
                proxies = future.result()
                all_proxies.extend(proxies)
            except Exception as e:
                print(f"爬取失败: {e}")

    # 去重
    unique_proxies = list(set(all_proxies))
    print(f"爬取完成，获取 {len(unique_proxies)} 个唯一代理")

    return unique_proxies

def test_proxy(proxy: str) -> Tuple[bool, Optional[float]]:
    """测试单个代理是否可用"""
    try:
        proxies = {
            "http": proxy,
            "https": proxy
        }

        start_time = time.time()
        response = requests.get(
            CONFIG["test_url"],
            proxies=proxies,
            timeout=CONFIG["timeout"]
        )
        end_time = time.time()

        response_time = end_time - start_time

        if response.status_code == 200:
            # 检查返回的IP是否与代理IP匹配
            try:
                data = response.json()
                if "origin" in data:
                    # 简单的验证：确保返回了数据
                    return True, response_time
            except:
                # 即使不是JSON格式，只要返回200也认为是成功的
                return True, response_time

        return False, response_time

    except Exception:
        return False, None

def validate_proxies(proxies: List[str]) -> List[str]:
    """验证代理可用性"""
    print(f"开始验证 {len(proxies)} 个代理...")

    valid_proxies = []
    total = len(proxies)

    with ThreadPoolExecutor(max_workers=CONFIG["validator_workers"]) as executor:
        futures = {executor.submit(test_proxy, proxy): proxy for proxy in proxies}

        completed = 0
        for future in as_completed(futures):
            completed += 1
            proxy = futures[future]

            try:
                is_valid, response_time = future.result()
                if is_valid and response_time and response_time <= CONFIG["max_response_time"]:
                    valid_proxies.append(proxy)

                # 每50个代理显示一次进度
                if completed % 50 == 0 or completed == total:
                    print(f"进度: {completed}/{total}，有效: {len(valid_proxies)}")

            except Exception as e:
                pass

    print(f"验证完成，有效代理: {len(valid_proxies)}/{total}")
    return valid_proxies

def merge_proxies(new_proxies: List[str], existing_proxies: List[str]) -> List[str]:
    """合并新旧代理"""
    all_proxies = list(set(existing_proxies + new_proxies))
    return all_proxies

def main():
    """主函数"""
    print("=== 极简代理收集器 ===")

    # 设置数据目录
    setup_data_dir()

    # 加载已有代理
    existing_proxies = load_existing_proxies()
    print(f"已有代理: {len(existing_proxies)} 个")

    # 爬取新代理
    new_proxies = crawl_proxies()
    if not new_proxies:
        print("没有获取到新代理")
        return

    # 验证新代理
    valid_new_proxies = validate_proxies(new_proxies)
    if not valid_new_proxies:
        print("没有有效的新代理")
        return

    # 合并代理
    all_proxies = merge_proxies(valid_new_proxies, existing_proxies)
    print(f"合并后总代理: {len(all_proxies)} 个")

    # 保存代理
    if save_proxies(all_proxies):
        print(f"[成功] 保存成功: {len(all_proxies)} 个代理已保存")
    else:
        print("[失败] 保存失败")

    print("=== 运行完成 ===")

if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[中断] 用户中断")
    except Exception as e:
        print(f"\n[错误] 运行失败: {e}")
        import traceback
        traceback.print_exc()