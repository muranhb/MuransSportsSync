# Sport Sync 🏃‍♂️🚴‍♀️🚶

> 🎉 **特别鸣谢**
> 
> 感谢 [yihong0618/running_page](https://github.com/yihong0618/running_page) 项目提供的基础代码。

---

## 🌟 项目简介

**Sport Sync** 的出现就是为了解决oppo等国内厂商数据封闭，暂时无法使用Sigma/Outbase等优秀的运动数据聚合平台进行运动数据的统一管理的困境。

该项目实现了从keep同步运动数据到佳明（中国/国际），能够导出配速、心率等运动数据。

---

## 🛠️ 准备工作
* Keep 登录手机号及密码。
* Garmin 登录邮箱及密码（清楚是中国区还是国际区账号）。

---

## 🚀 部署指引

本项目推荐直接使用 **GitHub Actions** 进行零成本的云端自动化部署。

### 1. Fork 本仓库
点击页面右上角的 `Fork` 按钮，将本项目克隆到你自己的 GitHub 账号下。

### 2. 配置环境变量 (Secrets)
进入你 Fork 后的仓库，依次点击 `Settings` -> 左侧边栏 `Secrets and variables` -> `Actions` -> `New repository secret`，添加以下 5 个必须的机密变量：

| Secret 名称 | 说明 | 示例 |
| --- | --- | --- |
| `KEEP_PHONE` | Keep 绑定的登录手机号 | `13800000000` |
| `KEEP_PASSWORD` | Keep 的登录密码 | `your_keep_password` |
| `GARMIN_EMAIL` | 佳明 (Garmin) 登录邮箱 | `example@email.com` |
| `GARMIN_PASSWORD`| 佳明 (Garmin) 登录密码 | `your_garmin_password`|
| `GARMIN_IS_CN` | 是否为佳明中国区账号（是填 `true`，国际区填 `false`） | `true` |

### 3. 启动并测试工作流
1. 点击仓库顶部的 `Actions` 标签页。
2. 允许并启用 GitHub Actions。
3. 在左侧选择 `Sport Data Sync Hub` 工作流。
4. 点击右侧的 `Run workflow` 按钮进行一次手动全量同步。
5. 运行结束后，你可以前往 Garmin Connect 查看同步过去的精美数据了！

---

## 💻 本地运行与调试

如果你希望在本地机器上运行或调试，请确保环境为 **Python 3.10+**。

```bash
# 1. 克隆代码到本地
git clone https://github.com/muranhb/sports-sync
cd sport-sync

# 2. 安装核心依赖
pip install -r requirements.txt

# 3. 动态获取佳明 Secret (中国区账号需加上 --is-cn)
python garmin/get_garmin_secret.py "你的佳明邮箱" "你的佳明密码" --is-cn

# 4. 执行核心同步脚本
python keep_to_garmin_sync.py "Keep手机号" "Keep密码" "超长Secret" --is-cn --sync-types running hiking cycling
```

## 📂 项目结构目录
本项目的架构经过精心设计，模块解耦，非常利于后续扩展（例如加入 Keep 到 Strava 的流转）。

```Plaintext
sport-sync/
├── config/
│   └── config.py               # 全局配置文件，定义路径及静态常量
├── outputs/                    # 运行时生成的临时目录 (已被 .gitignore 忽略)
│   ├── FIT_OUT/                # 转换生成的佳明原生 FIT 文件存放处
│   ├── GPX_OUT/                # GPX 文件存放处
│   └── TCX_OUT/                # Keep 下载的原始 TCX 文件存放处
├── tools/
│   ├── gpx2fit.py              # 提供 GPX 到 FIT 的转换工具（无心率等数据）
│   └── tcx2fit.py              # 提供 TCX 到 FIT 的高级转换工具
├── garmin/
│   ├── garmin_sync.py          # Garmin API 交互核心类 (下载/上传)
│   ├── garmin_device_adaptor.py# 佳明设备伪装适配器
│   └── get_garmin_secret.py    # 动态获取并刷新 Garth OAuth Token
├── keep/
│   └── keep_sync.py            # Keep API 交互核心类 (模拟登录/获取活动/下载TCX)
├── .github/workflows/
│   └── sport_sync.yml          # GitHub Actions 自动化工作流配置文件
├── util/
│   └── utils.py                # 通用工具函数 (时间处理、坐标转换等)
├── keep_to_garmin_sync.py      # 【核心启动脚本】Keep到佳明同步业务的主控文件
├── keep2garmin.json            # 自动生成的持久化轻量数据库 (用于去重)
├── requirements.txt            # 项目 Python 依赖
└── README.md                   # 项目说明文档
```