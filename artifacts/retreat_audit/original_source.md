# 退潮公式迁移审计

## 来源

| 项目 | 值 |
|---|---|
| 远端仓库 | https://github.com/healyirobert99-dotcom/zhuxian-catch |
| 完整 commit SHA | `357f6ee366ea2838faf2aa8ec0f9d2787c59c7af` |
| 源文件 | `scripts/generate_daily_review.py` |
| 文件 SHA256 | `d4f2cecf6a2b319b9baf525040f67fc575d1c98e3320e623c2927ab0a60b563d` |
| 本地副本 | `D:\zhuxian-catch_2026-06-24\scripts\generate_daily_review.py` |

## 横截面排名口径

| 项目 | 结果 |
|---|---|
| 排名对象 | 当日全部合格行业（stock_daily 中每日的 industry_daily 截面） |
| 排名方法 | `df[col].rank(pct=True)` — pandas 百分位排名（0~1） |
| 升序/降序 | 升序：值越大 rank 越高（接近 1.0）。退潮公式中 `(1 - rank)` 使弱值退潮分更高 |
| 同值处理 | 平均排名（pandas 默认） |
| 缺失值 | NaN 被跳过，rank 返回 NaN。过滤时忽略或返回空结果 |
| 当日行业全集 | `compute_industry_daily_metrics()` 输出当日所有行业，股票数 ≥ 20 |
| 逐日重新排名 | 是，每个交易日独立计算 |

## 新旧函数逐项映射

| 旧函数/位置 | 新函数 | 说明 |
|---|---|---|
| `generate_daily_review.py:949` — rank 列生成 | `build_retreat_cross_section()` 3 号步 | for col in rank_cols: rank(pct=True) |
| `generate_daily_review.py:952-960` — warming_score_raw | `build_retreat_cross_section()` 4 号步 | 九因子加权 |
| `generate_daily_review.py:963-972` — candidate_score_raw | `build_retreat_cross_section()` 4 号步 | 十因子加权 |
| `generate_daily_review.py:975-984` — confirmed_score_raw | `build_retreat_cross_section()` 4 号步 | 七因子加权 |
| `generate_daily_review.py:1245-1248` — retreat_penalty | `build_retreat_cross_section()` 惩罚逻辑 | retreat_condition → -25, mild → -10/-8 |
| `generate_daily_review.py:1251-1257` — retreat_score | `build_retreat_cross_section()` 5 号步 | 五因子上限 100 |
| `generate_daily_review.py:1258` — in_retreat_risk | `build_retreat_cross_section()` 5 号步 | ≥55 |
| `generate_daily_review.py:1349-1385` — classify_industry_stage | `classify_retreat_stage()` | 四阶段判定 |
| `generate_daily_review.py:1451-1474` — mainline_grade | `classify_retreat_action()` | 到三档映射 |

## 排名字段生成

所有带 `_rank` 后缀的字段通过以下代码生成：

```python
rank_cols = ["ret5", "ret10", "ret20", "ret60",
             "excess20", "excess60",
             "above20",
             "amount5_60", "amount10_60", "amount20_60"]
for col in rank_cols:
    last[col + "_rank"] = last[col].rank(pct=True)
```

排名对象：`last` 是当日全部通过 `min_stocks ≥ 20` 过滤的行业 DataFrame。
公式中 `(1 - ret5_rank)` 意味着：ret5 最弱的行业（rank 接近 0）→ 退潮分最高（接近 20）。

## 数据链追溯

| 字段 | 数据表 | 原始字段 | 窗口 | 缺失处理 |
|---|---|---|---|---|
| ret5 | industry_daily | 行业等权 5 日收益 | 5 个交易日 | NaN → 对应 rank 为 NaN |
| ret5_rank | 同上 | 当日横截面百分位 | 当日 | NaN 不参与排名 |
| drawdown20 | industry_daily | 20 日最大回撤 | 20 个交易日 | 默认 0 |
| above20 | industry_daily | 站上 MA20 的成分股比例 | 当日 | 默认 0 |
| above20_rank | 同上 | 当日横截面百分位 | 当日 | NaN 不参与排名 |
| amount5_60 | industry_daily | 5 日均量/60 日均量 | 60+5 个交易日 | 默认 0 |
| amount5_60_rank | 同上 | 当日横截面百分位 | 当日 | NaN 不参与排名 |
| confirmed_score_raw | industry_daily | 七因子加权（90 分制） | 当日 | 各项默认 0 |
| candidate_score_raw | industry_daily | 十因子加权（100 分制） | 当日 | 各项默认 0 |

所有字段的数据截止日期：`trade_date <= signal_data_date`。
所用价格口径：`daily_hfq`（后复权日线）。
所有排名与老系统一致：`rank(pct=True)` 在 `min_stocks ≥ 20` 的行业上运行。
