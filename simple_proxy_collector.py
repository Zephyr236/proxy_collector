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
import asyncio
import requests
import io
import re
import js2py
from bs4 import BeautifulSoup
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Tuple, Optional, Any
from datetime import datetime

# 设置标准输出编码为UTF-8
if sys.stdout.encoding is None or sys.stdout.encoding.upper() != 'UTF-8':
    sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding='utf-8', errors='replace')
if sys.stderr.encoding is None or sys.stderr.encoding.upper() != 'UTF-8':
    sys.stderr = io.TextIOWrapper(sys.stderr.buffer, encoding='utf-8', errors='replace')

# 简单配置
CONFIG = {
    "crawler_workers": 20,  # 增加爬虫工作者数量以支持更多源（现有20个源）
    "validator_workers": 10,
    "async_validator_concurrency": 50,  # 异步验证并发数（根据网络情况调整）
    "validation_method": "async",  # 验证方法：async（异步）或sync（同步，线程池）
    "timeout": 5,  # 单个代理测试超时时间（秒）
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
    """从Geonode获取代理（多页）"""
    proxies = []
    max_pages = 3  # 限制页数以避免请求过多
    max_retries = 2

    for page in range(1, max_pages + 1):
        url = f"https://proxylist.geonode.com/api/proxy-list?limit=500&page={page}&sort_by=lastChecked&sort_type=desc"

        for attempt in range(max_retries):
            try:
                timeout = CONFIG["timeout"] * (attempt + 1)
                response = requests.get(url, timeout=timeout)
                if response.status_code == 200:
                    data = response.json()
                    items = data.get("data", [])
                    if not items:
                        break  # 没有数据则停止翻页

                    for item in items:
                        ip = item.get("ip")
                        port = item.get("port")
                        protocols = item.get("protocols", [])

                        if ip and port and protocols:
                            protocol = protocols[0] if protocols else "http"
                            proxy = f"{protocol}://{ip}:{port}"
                            proxies.append(proxy)
                    print(f"Geonode 第 {page} 页: 获取 {len(items)} 个代理")
                    break  # 成功则退出重试循环
                else:
                    print(f"Geonode 第 {page} 页请求失败 (HTTP {response.status_code})，重试 {attempt+1}/{max_retries}")
            except Exception as e:
                if attempt == max_retries - 1:
                    print(f"Geonode 第 {page} 页爬取失败: {e}")
                else:
                    print(f"Geonode 第 {page} 页爬取失败，重试 {attempt+1}/{max_retries}: {e}")
                    time.sleep(2)

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

def fetch_proxyscrape_proxies() -> List[str]:
    """从ProxyScrape获取代理"""
    proxies = []
    protocols = ["http", "socks4", "socks5"]

    for protocol in protocols:
        try:
            # 直接请求原始URL
            url = f"https://api.proxyscrape.com/v2/?request=displayproxies&protocol={protocol}&timeout=10000&country=all&ssl=all&anonymity=all"
            response = requests.get(url, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                proxy_list = response.text.strip().split("\r\n")
                for proxy in proxy_list:
                    if proxy.strip():
                        # 调整socks5协议
                        proxy_protocol = protocol
                        if protocol == "socks5":
                            proxy_protocol = "socks5h"
                        proxies.append(f"{proxy_protocol}://{proxy}")
                print(f"ProxyScrape {protocol}: 获取 {len(proxy_list)} 个代理")
        except Exception as e:
            print(f"ProxyScrape {protocol} 爬取失败: {e}")

    return proxies


def fetch_roosterkid_proxies() -> List[str]:
    """从RoosterKid的GitHub仓库获取代理"""
    proxies = []
    sources = [
        ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS4.txt", "socks4"),
        ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/SOCKS5.txt", "socks5h"),
        ("https://raw.githubusercontent.com/roosterkid/openproxylist/main/HTTPS.txt", "https"),
    ]

    for url, protocol in sources:
        try:
            response = requests.get(url, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                lines = response.text.strip().split("\n")
                # 跳过标题行（前12行）
                data_lines = lines[12:] if len(lines) > 12 else lines
                count = 0
                for line in data_lines:
                    line = line.strip()
                    if line and not line.startswith("#"):
                        parts = line.split()
                        if len(parts) >= 2:
                            proxy = f"{protocol}://{parts[1]}"
                            proxies.append(proxy)
                            count += 1
                print(f"RoosterKid {protocol}: 获取 {count} 个代理")
        except Exception as e:
            print(f"RoosterKid {protocol} 爬取失败: {e}")

    return proxies


def fetch_proxifly_proxies() -> List[str]:
    """从proxifly/free-proxy-list获取代理"""
    proxies = []
    url = "https://cdn.jsdelivr.net/gh/proxifly/free-proxy-list@main/proxies/all/data.txt"

    try:
        response = requests.get(url, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            count = 0
            for line in lines:
                line = line.strip()
                if line:
                    # 原格式已经是完整代理，如 http://ip:port
                    # 将socks5替换为socks5h，socks4替换为socks4h
                    proxy = line
                    if "socks5://" in proxy:
                        proxy = proxy.replace("socks5://", "socks5h://")
                    elif "socks4://" in proxy:
                        # socks4保持原样，不需要socks4h
                        pass
                    proxies.append(proxy)
                    count += 1
            print(f"Proxifly Free Proxy List: 获取 {count} 个代理")
    except Exception as e:
        print(f"Proxifly Free Proxy List爬取失败: {e}")

    return proxies


def fetch_sockslist_us_proxies() -> List[str]:
    """从sockslist.us获取代理"""
    proxies = []
    url = "https://sockslist.us/Raw"

    try:
        response = requests.get(url, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            lines = response.text.strip().split("\n")
            count = 0
            for line in lines:
                line = line.strip()
                if line and ":" in line:
                    # 添加两种协议
                    proxies.append(f"socks5://{line}")
                    proxies.append(f"socks5h://{line}")
                    count += 2
            print(f"SocksList US: 获取 {count} 个代理")
    except Exception as e:
        print(f"SocksList US爬取失败: {e}")

    return proxies




def fetch_zdaye_proxies() -> List[str]:
    """从zdaye.com获取代理"""
    proxies = []
    max_pages = 5  # 爬取最多5页
    headers = {
        "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.3",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.5",
        "Accept-Encoding": "gzip, deflate, br",
        "Referer": "https://www.zdaye.com/",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
        "Sec-Fetch-Dest": "document",
        "Sec-Fetch-Mode": "navigate",
        "Sec-Fetch-Site": "same-origin",
        "Sec-Fetch-User": "?1",
        "Cache-Control": "max-age=0"
    }

    for page in range(1, max_pages + 1):
        try:
            url = f"https://www.zdaye.com/free/{page}/"
            response = requests.get(url, headers=headers, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, "html.parser")
                table_block_div = soup.find("div", class_="abox ov")
                if not table_block_div:
                    print(f"Zdaye 第 {page} 页: 未找到表格")
                    continue

                page_proxies = []
                for table in table_block_div.find_all("table"):
                    for tbody in table.find_all("tbody"):
                        for tr in tbody.find_all("tr"):
                            row_data = [td.get_text(strip=True) for td in tr.find_all("td")]
                            if len(row_data) >= 2:
                                ip = row_data[0]
                                port = row_data[1]
                                # 原爬虫使用 socks5 协议
                                proxy = f"socks5://{ip}:{port}"
                                page_proxies.append(proxy)
                print(f"Zdaye 第 {page} 页: 获取 {len(page_proxies)} 个代理")
                proxies.extend(page_proxies)
            else:
                print(f"Zdaye 第 {page} 页请求失败 (HTTP {response.status_code})")
        except Exception as e:
            print(f"Zdaye 第 {page} 页爬取失败: {e}")

    return proxies


def fetch_spys_one_proxies() -> List[str]:
    """从spys.one获取代理（使用JavaScript端口解码）"""
    # 使用spys_one项目中的cookies和headers（可能需要更新）
    cookies = {
        '_ga_XWX5S73YKH': 'GS2.1.s1769449589$o3$g1$t1769450226$j33$l0$h0',
        '_ga': 'GA1.1.1359544579.1769440694',
        'cf_clearance': 'VsncOXiIsWN2re2BgDUY_V3kWdwqP9kZO5pwK8R3o0Q-1769449587-1.2.1.1-ok1wH6F5lcq85aAlesC3P5z3RWR9zvkcjg4O_doBrzJZJjlk4OHfCcpvXcBlGdGzdzi63k1mGcABjj.mF2RDscx3ORO1UjA2AYTco8TcCjl7s7mYmGhfkdSv0pe1_va0CMiXuq6pQBoItfhnA.gn7exHakx4021moiKOMyDezsBfvt4u4HR_qbEXQxKgGqF65.wGb3W69w8nX4MH8QcODEEHNxkdiKTXe6iQ8iJuwWk',
        '__gads': 'ID=046e27d2b5f6af88:T=1769440694:RT=1769450197:S=ALNI_MbR6GNw7GvproRaDuOdzfNHphYHIA',
        '__gpi': 'UID=000011eb94f50545:T=1769440694:RT=1769450197:S=ALNI_Ma--28Sf8jk0c0dOggdMXrMnO-tlw',
        '__eoi': 'ID=d96e54866ede7675:T=1769440694:RT=1769450197:S=AA-AfjZLdPr_vYiyIxCRZowx7irr',
        'FCCDCF': '%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B32%2C%22%5B%5C%22481bf5c4-4d9b-42c8-a095-0ad81d454715%5C%22%2C%5B1769440698%2C131000000%5D%5D%22%5D%5D%5D',
        'FCNEC': '%5B%5B%22AKsRol8sf4vDs3kowdjuAukPey5BgPaWovJ9B2lgRkDBDnmSV1kJw4xidx0F1-q_2wDrmMm7-1GzKU4b_Le2d5qsqNYmOItuWak_FfpkA9QWLNW6qyblvCU8bU4RWg-fGHS-8al_NWFwvSr3DMWt5SouOz0Oi_PIGw%3D%3D%22%5D%5D',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Content-Type': 'application/x-www-form-urlencoded',
        'Origin': 'https://spys.one',
        'Connection': 'keep-alive',
        'Referer': 'https://spys.one/free-proxy-list/FR/',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    # POST数据以获取500条代理
    data = {
        'xx00': '',
        'xpp': '5',
        'xf1': '0',
        'xf2': '0',
        'xf4': '0',
        'xf5': '0',
    }

    countries = ["FR", "US", "RU", "HK", "JP", "BR", "SG", "ID", "FI", "TH", "CO", "MX"]

    class SpysOneCrawler:
        def __init__(self, cookies, headers, timeout):
            self.cookies = cookies
            self.headers = headers
            self.timeout = timeout
            self.vars_dict = None

        def fetch(self, url, data=None):
            """Fetch page content, use POST if data provided"""
            if data:
                resp = requests.post(url, cookies=self.cookies, headers=self.headers, data=data, timeout=self.timeout)
            else:
                resp = requests.get(url, cookies=self.cookies, headers=self.headers, timeout=self.timeout)
            resp.raise_for_status()
            return resp.text

        def decode_port_variables(self, html):
            """Extract and decode JavaScript variables for port obfuscation"""
            # Find eval code
            pattern = r'eval\(function\(p,r,o,x,y,s\)\{.*?\}\(.*?\)\)'
            match = re.search(pattern, html, re.DOTALL)
            if not match:
                raise ValueError("Could not find eval code")

            eval_code = match.group(0)

            # Extract function and arguments
            func_match = re.search(r"eval\(function\((.*?)\)\{(.*?)\}\((.*?)\)\)", eval_code, re.DOTALL)
            if not func_match:
                raise ValueError("Could not parse eval function")

            params, body, args = func_match.groups()

            # Use js2py to unpack
            context = js2py.EvalJs()
            js_code = f"var unpack = function({params}) {{{body}}}; var result = unpack({args}); result;"
            unpacked = context.eval(js_code)

            # Execute unpacked code to set variables
            context.execute(unpacked)

            # Collect known variables
            vars_dict = {}
            # Parse assignments from unpacked code
            lines = unpacked.split(';')
            for line in lines:
                line = line.strip()
                if '=' in line:
                    var, expr = line.split('=', 1)
                    var = var.strip()
                    try:
                        # Evaluate expression in context
                        vars_dict[var] = context.eval(expr)
                    except:
                        pass

            # Also get common variables directly
            common_vars = [
                'Two', 'Six', 'Four', 'Zero', 'Five', 'Nine', 'Three', 'Seven', 'Eight', 'One',
                'SevenZeroFour', 'EightOneThree', 'Six9Six', 'NineEightNine', 'FiveOneZero',
                'Four9One', 'ZeroTwoFive', 'Five6Seven', 'Nine5Two', 'Zero4Eight',
                'Four4OneSeven', 'Eight5SevenNine', 'TwoSevenNineSix', 'Five2TwoZero',
                'SixFiveThreeFive', 'OneFourEightTwo', 'Eight4FourThree', 'SevenTwoFiveOne',
                'EightFourSixFour', 'OneOneZeroEight'
            ]
            for var in common_vars:
                try:
                    vars_dict[var] = context[var]
                except:
                    pass

            self.vars_dict = vars_dict
            return vars_dict

        def evaluate_port_expression(self, expr):
            """Evaluate port expression like (EightFourSixFour^Six9Six)+(Four4OneSeven^Five6Seven)"""
            if not self.vars_dict:
                raise ValueError("Variables not decoded")

            # Find all XOR terms
            terms = re.findall(r'\(([^)]+)\)', expr)
            port_parts = []
            for term in terms:
                if '^' in term:
                    var1, var2 = term.split('^')
                    var1 = var1.strip()
                    var2 = var2.strip()
                    val1 = self.vars_dict.get(var1)
                    val2 = self.vars_dict.get(var2)
                    if val1 is None or val2 is None:
                        raise ValueError(f"Unknown variable: {var1} or {var2}")
                    port_parts.append(str(val1 ^ val2))
                else:
                    # Might be a direct number? Not likely
                    pass

            return ''.join(port_parts)

        def parse_proxies(self, html):
            """Parse proxy list from HTML"""
            soup = BeautifulSoup(html, 'html.parser')
            proxies = []

            # Find all table rows
            for row in soup.find_all('tr'):
                # Look for IP address in spy14 font
                ip_font = row.find('font', class_='spy14')
                if not ip_font:
                    continue

                # Check if there's a port script
                script = ip_font.find('script')
                if not script or not script.string:
                    continue

                # Extract IP (text before script)
                ip_text = ip_font.get_text(strip=True)
                # Remove script part
                ip = ip_text.split('<')[0] if '<' in ip_text else ip_text

                # Validate IP format
                if not re.match(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip):
                    continue

                # Extract port expression
                script_text = script.string
                match = re.search(r'document\.write\(":"\s*\+\s*(.*)\)', script_text)
                if not match:
                    continue

                expr = match.group(1)
                try:
                    port = self.evaluate_port_expression(expr)
                except Exception as e:
                    print(f"Error evaluating port for {ip}: {e}")
                    continue

                # Extract proxy type (second column)
                cols = row.find_all('td')
                if len(cols) < 2:
                    continue

                proxy_type = cols[1].get_text(strip=True)
                # Filter out non-proxy rows (like headers)
                if not proxy_type or 'HTTP' not in proxy_type and 'SOCKS' not in proxy_type:
                    continue

                # Determine protocol string
                proxy_type_lower = proxy_type.lower()
                if 'socks5' in proxy_type_lower:
                    protocol = 'socks5h'
                elif 'socks4' in proxy_type_lower:
                    protocol = 'socks4'
                elif 'https' in proxy_type_lower:
                    protocol = 'https'
                else:
                    protocol = 'http'

                proxies.append(f"{protocol}://{ip}:{port}")

            return proxies

        def crawl(self, url, data=None):
            """Main crawl function"""
            html = self.fetch(url, data=data)
            self.decode_port_variables(html)
            proxies = self.parse_proxies(html)
            return proxies

    # 主爬取逻辑
    all_proxies = []
    total_countries = len(countries)

    for i, country in enumerate(countries):
        # 更新Referer头为当前国家
        country_headers = headers.copy()
        country_headers['Referer'] = f'https://spys.one/free-proxy-list/{country}/'

        crawler = SpysOneCrawler(cookies, country_headers, CONFIG["timeout"])
        url = f'https://spys.one/free-proxy-list/{country}/'
        try:
            proxies = crawler.crawl(url, data=data)
            all_proxies.extend(proxies)
            print(f"Spys.one {country}: 获取 {len(proxies)} 个代理 ({i+1}/{total_countries})")
        except Exception as e:
            print(f"Spys.one {country} 爬取失败: {e}")
            # 继续下一个国家

    print(f"Spys.one 总计: 获取 {len(all_proxies)} 个代理")
    return all_proxies


def fetch_89ip_proxies() -> List[str]:
    """从89ip.cn获取代理"""
    proxies = []
    cookies = {
        'Hm_lvt_f9e56acddd5155c92b9b5499ff966848': '1769405985,1769487873',
        'https_waf_cookie': '449ae721-b8f7-4e2788eb780605cb75da84a6951d6640f8bc',
        'https_ydclearance': '9b7a0e30e98f253344b7cf2b-fd74-4005-892b-29dde2ea19da-1769495037',
        'Hm_lpvt_f9e56acddd5155c92b9b5499ff966848': '1769488390',
        'HMACCOUNT': 'E3C0C109BF9809D8',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Referer': 'https://www.89ip.cn/',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    max_pages = 6  # 爬取1-6页
    for page in range(1, max_pages + 1):
        url = f'https://www.89ip.cn/index_{page}.html'
        try:
            response = requests.get(url, cookies=cookies, headers=headers, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                response.encoding = 'utf-8'
                soup = BeautifulSoup(response.text, 'html.parser')
                table = soup.find('table', class_='layui-table')
                if not table:
                    table = soup.find('table')
                if not table:
                    continue
                rows = table.find_all('tr')
                page_proxies = []
                for row in rows:
                    cols = row.find_all('td')
                    if len(cols) >= 2:
                        ip = cols[0].get_text(strip=True)
                        port = cols[1].get_text(strip=True)
                        protocol = 'http'  # default
                        if len(cols) >= 3:
                            protocol_cell = cols[2].get_text(strip=True).lower()
                            if 'https' in protocol_cell:
                                protocol = 'https'
                            elif 'socks' in protocol_cell:
                                protocol = 'socks'
                        page_proxies.append(f"{protocol}://{ip}:{port}")
                proxies.extend(page_proxies)
                print(f"89ip 第 {page} 页: 获取 {len(page_proxies)} 个代理")
            else:
                print(f"89ip 第 {page} 页请求失败 (HTTP {response.status_code})")
        except Exception as e:
            print(f"89ip 第 {page} 页爬取失败: {e}")

    return proxies


def fetch_ip3366_proxies() -> List[str]:
    """从ip3366.net获取代理"""
    proxies = []
    cookies = {
        'Hm_lvt_c4dd741ab3585e047d56cf99ebbbe102': '1769405987,1769487848',
        'http_waf_cookie': '21538523-28f4-4006bb5e3a9d94958ed9f778498d59aa0412',
        'http_ydclearance': '139da8ff4b683447299ea746-791e-4686-9d03-90df3e211f08-1769495024',
        'Hm_lpvt_c4dd741ab3585e047d56cf99ebbbe102': '1769487972',
        'HMACCOUNT': 'EC176DE1D1C15F09',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Priority': 'u=0, i',
    }

    max_pages = 7  # 1-7页
    for page in range(1, max_pages + 1):
        params = {
            'stype': '1',
            'page': str(page),
        }
        try:
            response = requests.get('http://www.ip3366.net/', params=params, cookies=cookies, headers=headers, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                response.encoding = 'utf-8'
                # 使用正则表达式提取代理
                import re
                pattern = re.compile(r'<tr>\s*<td>([^<]+)</td>\s*<td>([^<]+)</td>\s*<td>[^<]+</td>\s*<td>([^<]+)</td>', re.IGNORECASE)
                matches = pattern.findall(response.text)
                page_proxies = []
                for ip, port, protocol in matches:
                    protocol = protocol.strip().lower()
                    if protocol not in ('http', 'https'):
                        if 'https' in protocol.lower():
                            protocol = 'https'
                        else:
                            protocol = 'http'
                    page_proxies.append(f"{protocol}://{ip}:{port}")
                proxies.extend(page_proxies)
                print(f"ip3366 第 {page} 页: 获取 {len(page_proxies)} 个代理")
            else:
                print(f"ip3366 第 {page} 页请求失败 (HTTP {response.status_code})")
        except Exception as e:
            print(f"ip3366 第 {page} 页爬取失败: {e}")
        # 礼貌延迟
        time.sleep(1)

    return proxies


def fetch_kuaidaili_proxies() -> List[str]:
    """从kuaidaili.com获取代理"""
    proxies = []
    cookies = {
        'channelid': '0',
        'sid': '1769405911253474',
        '_ss_s_uid': '472303e24f3eaa5b486647c73d70ce8f',
        '_ga_DC1XM0P4JL': 'GS2.1.s1769487896$o2$g1$t1769489288$j60$l0$h0',
        '_ga': 'GA1.1.776303152.1769406093',
        '_gcl_au': '1.1.919442339.1769406093',
        '_uetsid': '95fd0540fa7911f096430956bc926384|obrp2f|2|g32|0|2217',
        '_uetvid': '95fcf930fa7911f0af0c178f9c85ce6f|1w2t3le|1769489259162|9|1|bat.bing.com/p/conversions/c/h',
    }
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    base_urls = [
        'https://www.kuaidaili.com/free/dps/',  # 国内私密代理
        'https://www.kuaidaili.com/free/fps/',  # 国外代理
    ]

    for base_url in base_urls:
        for page in range(1, 4):  # 每类爬取3页
            url = f"{base_url}{page}"
            try:
                response = requests.get(url, cookies=cookies, headers=headers, timeout=CONFIG["timeout"])
                if response.status_code == 200:
                    response.encoding = 'utf-8'
                    soup = BeautifulSoup(response.text, 'html.parser')
                    page_proxies = []
                    tbody = soup.find('tbody', class_='kdl-table-tbody')
                    if tbody:
                        rows = tbody.find_all('tr')
                        for row in rows:
                            cols = row.find_all('td', class_='kdl-table-cell')
                            if len(cols) >= 4:
                                ip = cols[0].get_text(strip=True)
                                port = cols[1].get_text(strip=True)
                                protocol_raw = cols[3].get_text(strip=True).lower()
                                if 'https' in protocol_raw:
                                    protocol = 'https'
                                else:
                                    protocol = 'http'
                                page_proxies.append(f"{protocol}://{ip}:{port}")
                    proxies.extend(page_proxies)
                    print(f"kuaidaili {base_url} 第 {page} 页: 获取 {len(page_proxies)} 个代理")
                else:
                    print(f"kuaidaili {base_url} 第 {page} 页请求失败 (HTTP {response.status_code})")
            except Exception as e:
                print(f"kuaidaili {base_url} 第 {page} 页爬取失败: {e}")
            # 礼貌延迟
            time.sleep(1)

    # 去重
    unique_proxies = list(set(proxies))
    return unique_proxies


def fetch_proxylistplus_proxies() -> List[str]:
    """从proxylistplus.com获取代理"""
    proxies = []
    # 第一个请求的cookies和headers (Socks列表)
    cookies1 = {
        '_ga': 'GA1.2.199902941.1769488698',
        '_gid': 'GA1.2.892468389.1769488698',
        'cf_clearance': 'lJascryu2nrvuIiF0ak8WWr_TcBT61iK.f3lpF4KYbs-1769488721-1.2.1.1-MoNvY8ciQ2diFQrlebimLR8jinVzWgnhC5V_sRaG8ipDG_OdQpc5Gs8ZhBQC07jHMI7VyXgAKentTFYgIuZkbvpD5wjW81DDXppVPOWInsFkM9.8jjdHxZUUl_mP4MeKqsGzO461kLWKdrFjytUW47SY.TYLliD2UFR0h4E16Y4GnJNcuHUpj0rR084dxdkWER2BAOFtb0yK0wEMATwfAHoNboRGV8cdBkSTqDONMkU',
        '_no_tracky_100814458': '1',
        '_ga_Z3MSCTK1RG': 'GS2.2.s1769488703$o1$g1$t1769488725$j38$l0$h0',
    }
    # 第二个请求的cookies和headers (HTTP列表)
    cookies2 = cookies1.copy()
    cookies2['_ga_Z3MSCTK1RG'] = 'GS2.2.s1769488703$o1$g1$t1769488861$j60$l0$h0'
    cookies2['_gat'] = '1'

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
    }

    # 爬取Socks代理列表
    try:
        response = requests.get('https://list.proxylistplus.com/Socks-List-1', cookies=cookies1, headers=headers, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            socks_proxies = _extract_proxylistplus_proxies(response.text, default_protocol="socks")
            proxies.extend(socks_proxies)
            print(f"proxylistplus Socks: 获取 {len(socks_proxies)} 个代理")
    except Exception as e:
        print(f"proxylistplus Socks爬取失败: {e}")

    # 爬取HTTP代理列表
    try:
        response = requests.get('https://list.proxylistplus.com/Fresh-HTTP-Proxy-List-1', cookies=cookies2, headers=headers, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            http_proxies = _extract_proxylistplus_proxies(response.text, default_protocol="http")
            proxies.extend(http_proxies)
            print(f"proxylistplus HTTP: 获取 {len(http_proxies)} 个代理")
    except Exception as e:
        print(f"proxylistplus HTTP爬取失败: {e}")

    return proxies

def _extract_proxylistplus_proxies(html, default_protocol="http"):
    """从proxylistplus HTML中提取代理（内部辅助函数）"""
    if not html:
        return []
    import re
    soup = BeautifulSoup(html, 'lxml')
    proxies = []
    tables = soup.find_all('table')
    for table in tables:
        rows = table.find_all('tr')
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 2:
                continue
            ip = None
            port = None
            protocol = default_protocol
            # 方法1: 查找IP地址列
            for i, col in enumerate(cols):
                text = col.get_text(strip=True)
                if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', text):
                    ip = text
                    for offset in [1, -1, 2, -2]:
                        idx = i + offset
                        if 0 <= idx < len(cols):
                            port_text = cols[idx].get_text(strip=True)
                            if port_text.isdigit() and 1 <= int(port_text) <= 65535:
                                port = port_text
                                break
                    break
            # 方法2: 假设前两列是IP和端口
            if not ip and len(cols) >= 2:
                col1 = cols[0].get_text(strip=True)
                col2 = cols[1].get_text(strip=True)
                if re.match(r'^\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}$', col1) and col2.isdigit():
                    ip = col1
                    port = col2
            if ip and port:
                for col in cols:
                    text = col.get_text(strip=True).lower()
                    if 'socks4' in text:
                        protocol = 'socks4'
                        break
                    elif 'socks5' in text:
                        protocol = 'socks5'
                        break
                    elif 'socks' in text:
                        protocol = 'socks'
                    elif 'https' in text:
                        protocol = 'https'
                    elif 'http' in text:
                        protocol = 'http'
                proxies.append(f"{protocol}://{ip}:{port}")
    return proxies


def fetch_uu_proxy_proxies() -> List[str]:
    """从uu-proxy.com获取代理"""
    proxies = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'application/json, text/plain, */*',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Referer': 'https://uu-proxy.com/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }
    try:
        response = requests.get('https://uu-proxy.com/api/free', headers=headers, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            data = response.json()
            if data.get('success') and 'free' in data and 'proxies' in data['free']:
                proxy_list = data['free']['proxies']
                for proxy in proxy_list:
                    ip = proxy.get('ip')
                    port = proxy.get('port')
                    scheme = proxy.get('scheme')
                    if ip and port and scheme:
                        proxies.append(f"{scheme}://{ip}:{port}")
                print(f"uu-proxy: 获取 {len(proxies)} 个代理")
            else:
                print(f"uu-proxy API返回不成功或数据缺失: {data}")
        else:
            print(f"uu-proxy 请求失败 (HTTP {response.status_code})")
    except Exception as e:
        print(f"uu-proxy爬取失败: {e}")

    return proxies


def fetch_free_proxy_list_github() -> List[str]:
    """从databay-labs/free-proxy-list GitHub仓库获取代理"""
    proxies = []
    urls = {
        'http': 'https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/http.txt',
        'socks5': 'https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/socks5.txt',
        'https': 'https://raw.githubusercontent.com/databay-labs/free-proxy-list/refs/heads/master/https.txt'
    }

    for protocol, url in urls.items():
        try:
            response = requests.get(url, timeout=CONFIG["timeout"])
            response.raise_for_status()

            lines = response.text.strip().split('\n')
            count = 0

            for line in lines:
                line = line.strip()
                if not line:
                    continue

                # Parse ip:port format
                parts = line.split(':')
                if len(parts) >= 2:
                    ip = parts[0].strip()
                    port = parts[1].strip()

                    # Simple validation
                    if ip and port and port.isdigit():
                        proxy = f"{protocol}://{ip}:{port}"
                        proxies.append(proxy)
                        count += 1

            print(f"Free Proxy List GitHub {protocol}: 获取 {count} 个代理")

        except Exception as e:
            print(f"Free Proxy List GitHub {protocol} 爬取失败: {e}")

    return proxies


def fetch_nodemaven_proxies() -> List[str]:
    """从nodemaven.com获取代理"""
    proxies = []
    cookies = {
        '_gcl_au': '1.1.395836628.1769490509',
        'burst_uid': 'ecb17473968de00b4a2cfdb597d09235',
        'usetiful-visitor-ident': 'cd16cf01-63f0-4751-3c6a-a9c186a720b6',
        '_ga_TWZ9W1JNF7': 'GS2.1.s1769490514$o1$g1$t1769490666$j60$l0$h1214679402',
        '_ga': 'GA1.1.938034495.1769490515',
        '_ga_33JL89XFQ5': 'GS2.1.s1769490514$o1$g1$t1769490666$j60$l0$h949295014',
        'pys_session_limit': 'true',
        'pys_start_session': 'true',
        'pys_first_visit': 'true',
        'pysTrafficSource': 'google.com',
        'pys_landing_page': 'https://nodemaven.com/free-proxy-list/',
        'last_pysTrafficSource': 'google.com',
        'last_pys_landing_page': 'https://nodemaven.com/free-proxy-list/',
        'PAPVisitorId': 'NQOsAK66LrOZFB1lgf6zPyADtLjKUNvW',
        '_uetsid': '407aa4f0fb3e11f08e02df65b0a9dcf4',
        '_uetvid': '407a8c60fb3e11f0b62d07e6397ed1bf',
        '_ym_uid': '1769490524954226209',
        '_ym_d': '1769490524',
        '_ym_isad': '2',
        'intercom-id-yvkc0rpk': '38d805d8-fd48-40ec-a593-93081a40a772',
        'intercom-session-yvkc0rpk': '',
        'intercom-device-id-yvkc0rpk': 'df456bee-a130-4b86-83ce-b90d5e295b75',
        '_ym_visorc': 'w',
        'AMP_29d2d968b7': 'JTdCJTIyZGV2aWNlSWQlMjIlM0ElMjIxNGU4YWEyZi0zODc3LTQ4N2EtYTdhNS02NjJmZWQ3ODVlNmQlMjIlMkMlMjJzZXNzaW9uSWQlMjIlM0ExNzY5NDkwNTI2MDgwJTJDJTIyb3B0T3V0JTIyJTNBZmFsc2UlMkMlMjJsYXN0RXZlbnRUaW1lJTIyJTNBMTc2OTQ5MDUzMDk5MCUyQyUyMmxhc3RFdmVudElkJTIyJTNBNCUyQyUyMnBhZ2VDb3VudGVyJTIyJTNBMSU3RA==',
        'AMP_MKTG_29d2d968b7': 'JTdCJTIycmVmZXJyZXIlMjIlM0ElMjJodHRwcyUzQSUyRiUyRnd3dy5nb29nbGUuY29tJTJGJTIyJTJDJTIycmVmZXJyaW5nX2RvbWFpbiUyMiUzQSUyMnd3dy5nb29nbGUuY29tJTIyJTdE',
        '_clck': 'kwugn8%5E2%5Eg32%5E0%5E2218',
        '_clsk': '1ohse77%5E1769490535520%5E1%5E1%5Eh.clarity.ms%2Fcollect',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'application/json, text/javascript, */*; q=0.01',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'X-Requested-With': 'XMLHttpRequest',
        'Connection': 'keep-alive',
        'Referer': 'https://nodemaven.com/free-proxy-list/',
        'Sec-Fetch-Dest': 'empty',
        'Sec-Fetch-Mode': 'cors',
        'Sec-Fetch-Site': 'same-origin',
    }

    base_params = {
        'per_page': '100',
        'country': '',
        'protocol': '',
        'type': '',
        'latency': '',
    }

    for page in range(1, 6):
        params = base_params.copy()
        params['page'] = str(page)
        try:
            response = requests.get('https://nodemaven.com/wp-json/proxy-list/v1/proxies',
                                  params=params, cookies=cookies, headers=headers, timeout=CONFIG["timeout"])
            response.raise_for_status()
            data = response.json()
            # Response is a dict with 'proxies' key
            if isinstance(data, dict) and 'proxies' in data:
                proxy_list = data['proxies']
                count = 0
                for proxy in proxy_list:
                    ip = proxy.get('ip_address')
                    port = proxy.get('port')
                    protocol = proxy.get('protocol')
                    if ip and port and protocol:
                        # Convert protocol to lowercase for standard format
                        protocol_lower = protocol.lower()
                        proxies.append(f"{protocol_lower}://{ip}:{port}")
                        count += 1
                print(f"Nodemaven 第 {page} 页: 获取 {count} 个代理")
        except Exception as e:
            print(f"Nodemaven 第 {page} 页爬取失败: {e}")

    return proxies


def fetch_freeproxy_world_proxies() -> List[str]:
    """从freeproxy.world获取代理"""
    proxies = []
    cookies = {
        '_ga': 'GA1.1.1442368388.1769491256',
        '_gid': 'GA1.2.426402489.1769491256',
        '_ga_H19S2TE1ZB': 'GS2.1.s1769491256$o1$g1$t1769491958$j57$l0$h0',
        'cf_clearance': 'Z4_FxYLcTmVfovjVLRFgKSoIALWHiM1x8VuSOcxCYJg-1769491253-1.2.1.1-GMWlvnlWijcH9nGG82i6L2EzKa44X02mxHf9aj.Jx48B16QL4yY1aIPptpym1DeSV0FP1ITwtnxaos2eDCXY0D0MC.PzWhXTyhkiGiS1jQ_VeOph8wZqenZp1epVu6lbLK1bj9mtQ1gBwMYY2Wcl6yloWTNTkDY_P9OiCd.gfp2BbSZy3BKW1yp9x86H2xLqGv03camFsXWNGj6J0ukdoIco1yIdOIebOqAaKSfpdTA',
        '__gads': 'ID=9318efd53294ee26:T=1769491256:RT=1769491677:S=ALNI_MZVYWcWakVX-hy7VlpQXjxR_M3NJA',
        '__gpi': 'UID=00001332563e516b:T=1769491256:RT=1769491677:S=ALNI_MYgbvtqWNUstYh-TWnUkR2lRB1hKQ',
        '__eoi': 'ID=b7d19480640f7333:T=1769491256:RT=1769491677:S=AA-Afjbh0pNtJX5wgKDR75m2qVaT',
        'FCCDCF': '%5Bnull%2Cnull%2Cnull%2Cnull%2Cnull%2Cnull%2C%5B%5B32%2C%22%5B%5C%229c4ce7b4-b0eb-4267-b922-5efd65d3c3a3%5C%22%2C%5B1769491265%2C414000000%5D%5D%22%5D%5D%5D',
        'FCOEC': '%5B%5B%5B28%2C%22%5Bnull%2C%5Bnull%2C0%2C%5B1769491944%2C111942000%5D%2C1%5D%5D%22%5D]5D%5D',
        'FCNEC': '%5B%5B%22AKsRol9C7GvJ5Z8SXUljQI5dnaFlHAS7P9ThzH7M3x6csHsP9Clxjpvw92UU2b-ermqlGcTe9bKXspyq_cg1CyGX78jk3m-EVr-ryES6QhRhLyZB9H-6ychrFn30-lrO2joK4HVD8k-cdff6mhfyzmbPUvFfX3BxmQ%3D%3D%22%5D%2Cnull%2C%5B%5B21%2C%22%5B%5B%5B%5B5%2C1%2C%5B0%5D%5D%2C%5B1769491269%2C704339000%5D%2C%5B1209600%5D%5D%5D%5D%22%5D]5D%5D',
        '_gat_gtag_UA_138692554_2': '1',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'none',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    # 爬取第1页到第5页
    for page in range(1, 6):
        params = {
            'type': '',
            'anonymity': '',
            'country': '',
            'speed': '',
            'port': '',
            'page': str(page),
        }

        try:
            response = requests.get('https://www.freeproxy.world/',
                                  params=params,
                                  cookies=cookies,
                                  headers=headers,
                                  timeout=CONFIG["timeout"])
            response.raise_for_status()
        except Exception as e:
            print(f"FreeProxy.World 第 {page} 页请求失败: {e}")
            continue

        soup = BeautifulSoup(response.text, 'html.parser')

        # 找到代理表格
        table = soup.find('table', class_='table')
        if not table:
            print(f"FreeProxy.World 第 {page} 页: 未找到表格")
            continue

        tbody = table.find('tbody')
        if not tbody:
            print(f"FreeProxy.World 第 {page} 页: 未找到表格体")
            continue

        rows = tbody.find_all('tr')
        page_proxies = []
        for row in rows:
            cols = row.find_all('td')
            if len(cols) < 6:  # 需要至少6列
                continue

            try:
                # 提取IP地址
                ip = cols[0].text.strip()

                # 提取端口（可能在<a>标签内）
                port_elem = cols[1].find('a')
                port = port_elem.text.strip() if port_elem else cols[1].text.strip()

                # 提取协议类型（第6列，索引5）
                type_cell = cols[5]

                # 检查是否有徽章(badge)标签
                badges = type_cell.find_all('a', class_='badge')
                if badges:
                    # 取第一个徽章作为协议
                    protocol = badges[0].text.strip().lower()
                else:
                    # 直接提取文本
                    protocol = type_cell.text.strip().lower()

                # 标准化协议字符串
                if 'socks5' in protocol:
                    protocol = 'socks5'
                elif 'socks4' in protocol:
                    protocol = 'socks4'
                elif 'socks' in protocol:
                    protocol = 'socks'  # 通用socks
                elif 'https' in protocol:
                    protocol = 'https'
                else:
                    protocol = 'http'  # 默认HTTP

                proxy = f"{protocol}://{ip}:{port}"
                page_proxies.append(proxy)

            except Exception:
                # 跳过解析错误的行
                continue

        print(f"FreeProxy.World 第 {page} 页: 获取 {len(page_proxies)} 个代理")
        proxies.extend(page_proxies)

        # 添加延迟，避免请求过快
        if page < 5:
            time.sleep(1)

    return proxies


def fetch_proxydb_proxies() -> List[str]:
    """从proxydb.net获取代理"""
    proxies = []
    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Connection': 'keep-alive',
        'Referer': 'https://proxydb.net/',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'same-origin',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    # 辅助函数：从HTML提取代理
    def extract_proxies_from_html(html):
        soup = BeautifulSoup(html, 'html.parser')
        proxy_list = []

        # 方法1: 直接查找IP:Port格式的文本
        ip_port_pattern = re.compile(r'(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3})\s*[:：]\s*(\d{2,5})')
        matches = ip_port_pattern.findall(html)
        for ip, port in matches:
            protocol = 'http'  # 默认

            # 查找包含此IP:Port的HTML元素
            ip_port_text = f"{ip}:{port}"
            for element in soup.find_all(text=re.compile(re.escape(ip_port_text))):
                parent_text = element.parent.get_text().lower()
                if 'socks4' in parent_text:
                    protocol = 'socks4'
                elif 'socks5' in parent_text:
                    protocol = 'socks5'
                elif 'https' in parent_text:
                    protocol = 'https'
                elif 'http' in parent_text:
                    protocol = 'http'

                proxy_list.append((protocol, ip, port))
                break  # 找到第一个就跳出

        # 方法2: 查找表格结构
        table_selectors = [
            'table',
            '.table',
            '.proxy-table',
            '.proxy-list',
            'tbody'
        ]

        for selector in table_selectors:
            tables = soup.select(selector)
            for table in tables:
                rows = table.find_all('tr')
                for row in rows:
                    cells = row.find_all(['td', 'div', 'span'])
                    cell_texts = [cell.get_text(strip=True) for cell in cells]
                    for text in cell_texts:
                        ip_matches = re.findall(r'\b(?:\d{1,3}\.){3}\d{1,3}\b', text)
                        if ip_matches:
                            ip = ip_matches[0]
                            port_matches = re.findall(r'\b(\d{2,5})\b', text)
                            for port in port_matches:
                                if port != ip.split('.')[-1]:
                                    protocol = 'http'
                                    row_text = row.get_text().lower()
                                    if 'socks4' in row_text:
                                        protocol = 'socks4'
                                    elif 'socks5' in row_text:
                                        protocol = 'socks5'
                                    elif 'https' in row_text:
                                        protocol = 'https'

                                    proxy_list.append((protocol, ip, port))
                                    break

        # 去重
        unique_proxies = []
        seen = set()
        for protocol, ip, port in proxy_list:
            key = f"{ip}:{port}"
            if key not in seen:
                seen.add(key)
                unique_proxies.append((protocol, ip, port))

        return unique_proxies

    base_url = 'https://proxydb.net/'

    # 爬取多个offset页面
    for offset in range(0, 151, 30):
        params = {'offset': str(offset)}
        try:
            response = requests.get(base_url, params=params, headers=headers, timeout=CONFIG["timeout"])
            response.raise_for_status()

            extracted = extract_proxies_from_html(response.text)
            count = 0
            for protocol, ip, port in extracted:
                proxies.append(f"{protocol}://{ip}:{port}")
                count += 1

            print(f"ProxyDB offset={offset}: 获取 {count} 个代理")

        except Exception as e:
            print(f"ProxyDB offset={offset} 爬取失败: {e}")
            continue

    return proxies


def fetch_proxy5_proxies() -> List[str]:
    """从proxy5.net获取代理"""
    proxies = []
    # 尝试导入cloudscraper，如果不可用则跳过
    try:
        import cloudscraper
    except ImportError:
        print("Proxy5: cloudscraper模块未安装，跳过爬取")
        return proxies

    # 用户提供的cookies和headers
    cookies = {
        '_ga_2ZGKN4M0P5': 'GS2.1.s1769491268$o1$g0$t1769491268$j60$l0$h0',
        '_ga': 'GA1.1.1858901822.1769491268',
        '_gcl_au': '1.1.830845841.1769491268',
        '_ym_uid': '1769491301957709155',
        '_ym_d': '1769491301',
        '_ym_isad': '2',
        'cf_clearance': 'mcKP.1Sy6F1LN39L6Lt1sHb4Z.uUv4kOh_M3b.ahuQA-1769491300-1.2.1.1-dQwXgN9lzisPgD3wjZeUIGM37gol8xQt58i4gF0wUPSkiY4wu4fGbEIGY_AARwg1GH9TeELFczAG1i7zF0fxSM1EVKyT9WH0pdaUxP3af98183TmD_YoJqvtzN7JqwDGnbBtafqbeuRXnC.9eaEcwR9XFHLIPLOWRWsVsOX1B6DMNn.g9y3Rz7RLlvbZ531uVtAlVNMzhNGbLONJHmcSYCX1rsmnX63Wzz3F5kVg_5A',
        '_ym_visorc': 'w',
    }

    headers = {
        'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:147.0) Gecko/20100101 Firefox/147.0',
        'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
        'Accept-Language': 'zh-CN,zh;q=0.9,zh-TW;q=0.8,zh-HK;q=0.7,en-US;q=0.6,en;q=0.5',
        'Referer': 'https://www.google.com/',
        'Connection': 'keep-alive',
        'Upgrade-Insecure-Requests': '1',
        'Sec-Fetch-Dest': 'document',
        'Sec-Fetch-Mode': 'navigate',
        'Sec-Fetch-Site': 'cross-site',
        'Sec-Fetch-User': '?1',
        'Priority': 'u=0, i',
    }

    # 需要爬取的国家/地区列表
    countries = [
        'hong-kong',
        'canada',
        'usa',
        'india',
        'japan',
        'germany',
        'france',
        'united-kingdom',
    ]

    base_url = 'https://proxy5.net/free-proxy/'

    def normalize_protocol(proto):
        """将协议字符串标准化为小写形式"""
        proto_lower = proto.lower()
        if 'socks' in proto_lower:
            # 统一使用 socks5
            return 'socks5'
        elif 'https' in proto_lower:
            return 'https'
        else:
            # 默认为 http
            return 'http'

    scraper = cloudscraper.create_scraper()

    for country in countries:
        url = base_url + country
        try:
            response = scraper.get(url, cookies=cookies, headers=headers, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                soup = BeautifulSoup(response.text, 'html.parser')
                page_proxies = []
                # 查找所有包含IP地址的td元素
                for td in soup.find_all('td'):
                    strong = td.find('strong')
                    if strong:
                        ip = strong.text.strip()
                        # 简单验证IP地址格式
                        import re
                        if re.match(r'\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}', ip):
                            # 找到父级tr
                            tr = td.find_parent('tr')
                            if tr:
                                # 获取该行所有td
                                tds = tr.find_all('td')
                                if len(tds) >= 4:
                                    # IP是第一个td
                                    # 端口是第二个td
                                    port = tds[1].text.strip()
                                    # 协议是第三个td
                                    protocol_raw = tds[2].text.strip()
                                    protocol = normalize_protocol(protocol_raw)
                                    page_proxies.append(f"{protocol}://{ip}:{port}")
                proxies.extend(page_proxies)
                print(f"Proxy5 {country}: 获取 {len(page_proxies)} 个代理")
        except Exception as e:
            print(f"Proxy5 {country} 爬取失败: {e}")
        # 延迟一下，避免请求过快
        time.sleep(1)

    return proxies


def fetch_hookzof_proxies() -> List[str]:
    """从hookzof/socks5_list GitHub仓库获取SOCKS5代理"""
    proxies = []
    url = "https://raw.githubusercontent.com/hookzof/socks5_list/master/proxy.txt"

    try:
        response = requests.get(url, timeout=CONFIG["timeout"])
        if response.status_code == 200:
            lines = response.text.strip().split('\n')
            count = 0
            for line in lines:
                line = line.strip()
                if line and ':' in line:
                    # Split IP and port
                    ip_port = line.split(':', 1)
                    if len(ip_port) == 2:
                        ip = ip_port[0].strip()
                        port = ip_port[1].strip()
                        if ip and port:
                            # 移除端口中的非数字字符
                            import re
                            port_clean = ''.join(filter(str.isdigit, port))
                            if port_clean:
                                proxies.append(f"socks5://{ip}:{port_clean}")
                                count += 1
            print(f"Hookzof SOCKS5: 获取 {count} 个代理")
    except Exception as e:
        print(f"Hookzof SOCKS5 爬取失败: {e}")

    return proxies


def fetch_ebrasha_proxies() -> List[str]:
    """从多个GitHub仓库获取代理（ebrasha, stormsia, iplocate, vakhov）"""
    proxies = []
    # 代理URL列表
    proxy_urls = [
        ("https://raw.githubusercontent.com/ebrasha/abdal-proxy-hub/refs/heads/main/http-proxy-list-by-EbraSha.txt", "http"),
        ("https://raw.githubusercontent.com/ebrasha/abdal-proxy-hub/refs/heads/main/https-proxy-list-by-EbraSha.txt", "https"),
        ("https://raw.githubusercontent.com/ebrasha/abdal-proxy-hub/refs/heads/main/socks4-proxy-list-by-EbraSha.txt", "socks4"),
        ("https://raw.githubusercontent.com/ebrasha/abdal-proxy-hub/refs/heads/main/socks5-proxy-list-by-EbraSha.txt", "socks5"),
        ("https://raw.githubusercontent.com/stormsia/proxy-list/refs/heads/main/working_proxies.txt", "http"),  # 默认HTTP
        ("https://raw.githubusercontent.com/iplocate/free-proxy-list/refs/heads/main/all-proxies.txt", "http"),  # 默认HTTP
        ("https://raw.githubusercontent.com/vakhov/fresh-proxy-list/refs/heads/master/http.txt", "http"),
        ("https://github.com/vakhov/fresh-proxy-list/raw/refs/heads/master/https.txt", "https"),
        ("https://github.com/vakhov/fresh-proxy-list/raw/refs/heads/master/socks4.txt", "socks4"),
        ("https://github.com/vakhov/fresh-proxy-list/raw/refs/heads/master/socks5.txt", "mixed"),  # 特殊处理，文件中已包含协议
    ]

    import re
    ip_port_pattern = re.compile(r'^(\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}):(\d+)$')
    protocol_pattern = re.compile(r'^(socks[45]|http|https)://\d{1,3}\.\d{1,3}\.\d{1,3}\.\d{1,3}:\d+$')

    for url, protocol in proxy_urls:
        try:
            response = requests.get(url, timeout=CONFIG["timeout"])
            if response.status_code == 200:
                content = response.text
                lines = content.splitlines()
                count = 0
                for line in lines:
                    line = line.strip()
                    if not line or line.startswith('#'):
                        continue

                    # 如果协议是mixed，检查是否已包含协议
                    if protocol == "mixed":
                        if protocol_pattern.match(line):
                            proxies.append(line)
                            count += 1
                        # 也可能有未加协议的IP:端口
                        elif ip_port_pattern.match(line):
                            # 无法确定协议，跳过
                            pass
                    else:
                        match = ip_port_pattern.match(line)
                        if match:
                            ip, port = match.groups()
                            proxies.append(f"{protocol}://{ip}:{port}")
                            count += 1
                print(f"Ebrasha {url.split('/')[3]}: 获取 {count} 个代理")
        except Exception as e:
            print(f"Ebrasha {url} 爬取失败: {e}")

    return proxies


def crawl_proxies() -> List[str]:
    """爬取所有代理源"""
    print("开始爬取代理...")

    all_proxies = []

    # 从多个源爬取
    sources = [
        fetch_geonode_proxies,
        fetch_free_proxy_list,
        fetch_proxyscrape_proxies,
        fetch_roosterkid_proxies,
        fetch_proxifly_proxies,
        fetch_sockslist_us_proxies,
        fetch_zdaye_proxies,
        fetch_spys_one_proxies,
        fetch_89ip_proxies,
        fetch_ip3366_proxies,
        fetch_kuaidaili_proxies,
        fetch_proxylistplus_proxies,
        fetch_uu_proxy_proxies,
        fetch_free_proxy_list_github,
        fetch_nodemaven_proxies,
        fetch_freeproxy_world_proxies,
        fetch_proxydb_proxies,
        fetch_proxy5_proxies,
        fetch_hookzof_proxies,
        fetch_ebrasha_proxies,
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

async def test_proxy_async(proxy: str, semaphore: asyncio.Semaphore) -> Tuple[bool, Optional[float]]:
    """异步测试单个代理是否可用"""
    # 动态导入异步依赖
    try:
        import aiohttp
        from aiohttp_socks import ProxyConnector
    except ImportError:
        # 如果异步依赖未安装，抛出异常让外层处理
        raise ImportError("aiohttp 或 aiohttp_socks 未安装，请运行: pip install -r simple_requirements.txt")

    async with semaphore:
        try:
            start_time = time.time()

            # 设置超时
            timeout = aiohttp.ClientTimeout(total=CONFIG["timeout"])

            # 根据代理协议类型选择不同的连接方式
            if proxy.startswith('socks5://') or proxy.startswith('socks4://'):
                # SOCKS代理，使用aiohttp_socks
                try:
                    connector = ProxyConnector.from_url(proxy)
                    async with aiohttp.ClientSession(connector=connector, timeout=timeout) as session:
                        async with session.get(CONFIG["test_url"]) as response:
                            end_time = time.time()
                            response_time = end_time - start_time

                            if response.status == 200:
                                # 检查返回的IP是否与代理IP匹配
                                try:
                                    data = await response.json()
                                    if "origin" in data:
                                        return True, response_time
                                except:
                                    # 即使不是JSON格式，只要返回200也认为是成功的
                                    return True, response_time

                            return False, response_time
                except asyncio.TimeoutError:
                    return False, None
                except Exception:
                    return False, None
            else:
                # HTTP/HTTPS代理，使用aiohttp内置代理支持
                # 确保代理URL有协议头
                if not proxy.startswith('http://') and not proxy.startswith('https://'):
                    proxy_url = f"http://{proxy}"
                else:
                    proxy_url = proxy

                try:
                    async with aiohttp.ClientSession(timeout=timeout) as session:
                        async with session.get(CONFIG["test_url"], proxy=proxy_url) as response:
                            end_time = time.time()
                            response_time = end_time - start_time

                            if response.status == 200:
                                # 检查返回的IP是否与代理IP匹配
                                try:
                                    data = await response.json()
                                    if "origin" in data:
                                        return True, response_time
                                except:
                                    # 即使不是JSON格式，只要返回200也认为是成功的
                                    return True, response_time

                            return False, response_time
                except asyncio.TimeoutError:
                    return False, None
                except Exception:
                    return False, None

        except Exception:
            return False, None

async def validate_proxies_async(proxies: List[str]) -> List[str]:
    """异步验证代理可用性 - 使用任务队列控制并发"""
    print(f"开始异步验证 {len(proxies)} 个代理...")

    valid_proxies = []
    total = len(proxies)

    if total == 0:
        return []

    # 创建信号量限制并发数
    semaphore = asyncio.Semaphore(CONFIG["async_validator_concurrency"])

    # 使用集合来跟踪运行中的任务
    pending_tasks = {}
    completed_count = 0
    next_proxy_index = 0

    # 初始填充任务，不超过并发限制
    while next_proxy_index < total and len(pending_tasks) < CONFIG["async_validator_concurrency"]:
        proxy = proxies[next_proxy_index]
        task = asyncio.create_task(test_proxy_async(proxy, semaphore))
        pending_tasks[task] = proxy
        next_proxy_index += 1

    try:
        while pending_tasks:
            # 等待至少一个任务完成
            done, pending = await asyncio.wait(
                set(pending_tasks.keys()),  # 转换为集合
                return_when=asyncio.FIRST_COMPLETED,
                timeout=CONFIG["timeout"] * 2  # 超时时间稍长一些
            )

            # 处理超时情况（没有任务完成）
            if not done:
                # 检查是否有任务卡住，取消所有任务并重新开始
                print(f"[警告] 等待任务完成超时，取消 {len(pending_tasks)} 个任务")
                # 将取消的任务计入完成数量
                cancelled_tasks = list(pending_tasks.keys())
                for task in cancelled_tasks:
                    task.cancel()
                # 等待取消完成
                await asyncio.gather(*cancelled_tasks, return_exceptions=True)
                # 更新完成计数
                completed_count += len(cancelled_tasks)
                # 清空pending_tasks，重新开始
                pending_tasks.clear()
                # 重新添加任务，从当前索引开始
                while next_proxy_index < total and len(pending_tasks) < CONFIG["async_validator_concurrency"]:
                    proxy = proxies[next_proxy_index]
                    task = asyncio.create_task(test_proxy_async(proxy, semaphore))
                    pending_tasks[task] = proxy
                    next_proxy_index += 1
                continue

            # 处理完成的任务
            for task in done:
                proxy = pending_tasks.pop(task, None)
                if proxy is None:
                    # 任务可能已经被移除了
                    continue

                try:
                    is_valid, response_time = task.result()
                    if is_valid and response_time and response_time <= CONFIG["max_response_time"]:
                        valid_proxies.append(proxy)
                except asyncio.CancelledError:
                    # 任务被取消，忽略
                    pass
                except Exception:
                    # 单个代理验证失败，忽略
                    pass

                completed_count += 1

                # 根据总数动态调整进度显示频率
                if total > 10000:
                    update_interval = 500  # 大量代理时每500个更新一次
                elif total > 1000:
                    update_interval = 100   # 中等数量代理每100个更新一次
                else:
                    update_interval = 50    # 少量代理每50个更新一次

                if completed_count % update_interval == 0 or completed_count == total:
                    percent = (completed_count / total) * 100
                    print(f"进度: {completed_count}/{total} ({percent:.1f}%)，有效: {len(valid_proxies)}")

            # 添加新任务以保持并发数
            while next_proxy_index < total and len(pending_tasks) < CONFIG["async_validator_concurrency"]:
                proxy = proxies[next_proxy_index]
                task = asyncio.create_task(test_proxy_async(proxy, semaphore))
                pending_tasks[task] = proxy
                next_proxy_index += 1

    except Exception as e:
        print(f"[警告] 异步验证异常: {e}")
        # 取消所有剩余任务
        for task in pending_tasks:
            task.cancel()
        # 等待所有任务被取消
        await asyncio.gather(*pending_tasks.keys(), return_exceptions=True)
        raise

    print(f"异步验证完成，有效代理: {len(valid_proxies)}/{total}")
    return valid_proxies

def validate_proxies(proxies: List[str]) -> List[str]:
    """验证代理可用性，根据配置选择异步或同步验证"""
    if CONFIG["validation_method"] == "async":
        # 尝试使用异步验证
        try:
            return asyncio.run(validate_proxies_async(proxies))
        except ImportError as e:
            print(f"[警告] 异步验证依赖未安装，回退到同步验证: {e}")
            print("请运行: pip install -r simple_requirements.txt")
            # 回退到同步验证
            CONFIG["validation_method"] = "sync"
        except Exception as e:
            print(f"[警告] 异步验证失败，回退到同步验证: {e}")
            CONFIG["validation_method"] = "sync"

    if CONFIG["validation_method"] == "sync":
        # 使用同步线程池验证（向后兼容）
        print(f"开始同步验证 {len(proxies)} 个代理...")

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

                    # 根据总数动态调整进度显示频率
                    if total > 10000:
                        update_interval = 500  # 大量代理时每500个更新一次
                    elif total > 1000:
                        update_interval = 100   # 中等数量代理每100个更新一次
                    else:
                        update_interval = 50    # 少量代理每50个更新一次

                    if completed % update_interval == 0 or completed == total:
                        percent = (completed / total) * 100
                        print(f"进度: {completed}/{total} ({percent:.1f}%)，有效: {len(valid_proxies)}")

                except Exception as e:
                    pass

        print(f"同步验证完成，有效代理: {len(valid_proxies)}/{total}")
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
    valid_new_proxies = validate_proxies(new_proxies+existing_proxies)
    if not valid_new_proxies:
        print("没有有效的新代理")
        return

    # 合并代理
    all_proxies = merge_proxies(valid_new_proxies, [])
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
