# 极简代理收集器

一个极简的代理爬取、验证和保存工具，仅包含核心功能。

## 功能
- 从多个源爬取代理
- 验证代理可用性
- 保存为JSON格式
- 支持GitHub Actions定时运行

## 文件说明
- `simple_proxy_collector.py` - 主程序
- `simple_requirements.txt` - 依赖包
- `.github/workflows/proxy-collection.yml` - GitHub Actions工作流
- `data/proxies.json` - 保存的代理数据

## 本地运行
```bash
pip install -r simple_requirements.txt
python simple_proxy_collector.py
```

## GitHub Actions
工作流配置为每6小时自动运行一次，也可以手动触发。

自动保存结果到仓库的 `data/proxies.json` 文件中。

## 配置
在 `simple_proxy_collector.py` 中可以修改：
- 爬虫线程数
- 验证线程数
- 超时时间
- 测试URL
- 最大响应时间

## 数据格式
```json
{
  "version": "1.0",
  "last_updated": "2026-01-25T17:05:17Z",
  "total_proxies": 15,
  "proxies": [
    "http://38.180.189.145:80",
    "http://114.31.15.190:2024"
  ]
}
```