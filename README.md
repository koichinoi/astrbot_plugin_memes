# AstrBot 表情包制作插件（Python 引擎 · 图片面板）

`表情指令`、`表情面板`、`表情菜单` 会发送分区排版的 PNG 帮助面板。表情列表、搜索结果、模板详情、图片工具和调用统计也使用图片展示，不再依赖 AstrBot 将大段文字转换成图片。模板列表每页 30 项，搜索支持 `表情搜索 关键词 [页码]`。

面板在独立 Python 引擎进程中绘制。默认自动寻找系统中文字体；需要指定字体时，在插件配置中填写 `panel_font_path`。此更新继续使用 `meme-generator==0.1.14`，不改变原来的开关和统计数据。

将 [MemeCrafters/nonebot-plugin-memes](https://github.com/MemeCrafters/nonebot-plugin-memes) 的表情制作、搜索、开关、统计及图片工具移植到 AstrBot。模板和绘图使用 [MemeCrafters/meme-generator](https://github.com/MemeCrafters/meme-generator) **Python 引擎**，插件负责 AstrBot 消息与权限适配。

默认使用 Python 引擎内置的 295 个模板，不预装第三方扩展表情库。仓库仅包含插件源码、文档和测试，不包含运行环境、下载素材、会话记录或个人配置。

## 移植基线与依赖

- NoneBot 插件基线：`0.8.1`，提交 [`836d4414c280a0c133018df2b311be4d153fe97d`](https://github.com/MemeCrafters/nonebot-plugin-memes/commit/836d4414c280a0c133018df2b311be4d153fe97d)。
- 生成引擎：[`MemeCrafters/meme-generator`](https://github.com/MemeCrafters/meme-generator) 的 Python 包，固定为 [`meme-generator==0.1.14`](https://pypi.org/project/meme-generator/0.1.14/)，避免安装到使用不同接口的 0.2.x 包。
- 引擎以 Python 实现模板，使用 Pillow、pil-utils 和 skia-python 等绘图库。它在插件管理的独立 Python 环境中运行，通过本地子进程与 AstrBot 交换数据。
- Python 引擎依赖 Pillow 10.x，而 AstrBot 4.29.1 要求 Pillow 11.2.1 或以上，因此不将两套依赖安装进同一个环境。首次启动时插件自动建立独立引擎环境并安装 `engine_requirements.txt`。
- 建议 Python `3.12`。`metadata.yaml` 当前声明 AstrBot `>=4.0.0`；实际验证环境与检查范围见 [VALIDATION.md](VALIDATION.md)。
- 不依赖 NoneBot，也不需要配置大模型或单独运行生成器 HTTP 服务。

本版为 `v1.2.1`。`meme-generator` 在 PyPI 上的 0.1.x 是上述 Python 项目，0.2.0 起则是 Rust 项目的 Python 绑定，二者同名但接口不同，详见[上游安装说明](https://github.com/MemeCrafters/meme-generator/wiki/本地安装)。本插件已经按 Python 0.1.14 的接口重写适配层。

## 安装

### 从 GitHub 安装

在 AstrBot WebUI 的插件页选择从仓库链接安装，填写：

```text
https://github.com/koichinoi/astrbot_plugin_memes
```

安装后重载插件，等待首次引擎安装和素材下载完成，再发送 `表情面板`。命令默认无需 `/` 前缀。

### 从 ZIP 安装

1. 在 AstrBot WebUI 的插件页使用 ZIP／本地文件安装入口，上传本插件压缩包。
2. 确认插件自身依赖安装成功。在 **运行 AstrBot 的同一个 Python 环境** 中，必要时执行：

   ```bash
   python -m pip install -r data/plugins/astrbot_plugin_memes/requirements.txt
   ```

3. 重启 AstrBot，查看日志中的 `[memes]` 记录。首次启动会建立独立引擎环境并安装 Python 生成器，需要联网。
4. 等待引擎就绪后，在聊天中发送 `表情指令` 或 `表情列表`。

使用 Docker 时，应在 AstrBot 容器内安装依赖。不要只在宿主机的其他 Python 环境中安装。

### 从之前的安装包更新

用本版插件文件覆盖 `data/plugins/astrbot_plugin_memes/`，然后在运行 AstrBot 的同一个 Python 环境中重新安装插件自身依赖：

```bash
python -m pip install -r data/plugins/astrbot_plugin_memes/requirements.txt
```

重启 AstrBot 后，插件自动在独立环境中安装 `meme-generator==0.1.14`，不使用 AstrBot 主环境中先前安装的 0.2.x。原来的会话开关和调用统计仍保存在插件数据目录中。

如果自行管理已有的独立引擎环境，在**那个独立环境**内执行 `python -m pip install -r data/plugins/astrbot_plugin_memes/engine_requirements.txt`，再将本插件的 `engine_python_executable` 配置设为该环境的 Python 可执行文件绝对路径。此固定版本约束也能将独立环境中已有的 0.2.x 替换为 0.1.14，无需先手动卸载。

### 手动安装

将插件文件夹放到 AstrBot 安装目录的 `data/plugins/astrbot_plugin_memes/`，确认 `main.py` 和 `metadata.yaml` 直接位于此文件夹下，避免多套一层目录：

```text
AstrBot/
└── data/plugins/astrbot_plugin_memes/
    ├── main.py
    ├── backend.py
    ├── engine_backend.py
    ├── engine_worker.py
    ├── parser.py
    ├── state.py
    ├── metadata.yaml
    ├── _conf_schema.json
    ├── requirements.txt
    └── engine_requirements.txt
```

随后按上面的步骤安装依赖并重启。

### 独立引擎环境

`engine_python_executable` 留空时，插件在 AstrBot 的插件数据目录下建立 `python-engine/venv/` 虚拟环境，通常是 `data/plugin_data/astrbot_plugin_memes/python-engine/venv/`。它在首次准备或引擎依赖清单变化时安装依赖，后续复用环境。安装日志位于 `python-engine/install.log`，引擎诊断日志位于 `python-engine/engine.log`。运行 AstrBot 的 Python 需要支持 `venv` 和 `pip`；首次启动时需要访问 Python 包下载源。

若要离线部署或自行维护依赖，先创建一个独立 Python 环境并在其中安装本插件的 `engine_requirements.txt`，再将 `engine_python_executable` 填为那个环境的 Python 可执行文件路径。填写路径时插件不自动安装依赖。不要将它设为 AstrBot 的主环境，也不要将 `engine_requirements.txt` 安装进 AstrBot 的主环境。

### 首次资源下载

`check_resources_on_startup` 默认开启。首次启动会在后台调用 Python 引擎的 `check_resources()` 检查并下载模板图片，可能耗时较长，下载错误会写入 AstrBot 日志。资源准备完成前，部分模板可能提示缺少资源。

图片会下载到独立引擎环境中的 `meme_generator/memes/` 目录，运行 AstrBot 的账户需要对该目录有写权限。自动下载只处理模板图片，**不会安装字体**。关闭启动检查不会自动补齐缺失图片。

字体需要按[上游字体安装说明](https://github.com/MemeCrafters/meme-generator/wiki/本地安装#字体安装)，将 [resources/fonts](https://github.com/MemeCrafters/meme-generator/tree/main/resources/fonts) 中所需字体安装到运行 AstrBot 的系统或容器内。Linux 安装后刷新字体缓存；Docker 宿主机上的字体不会自动进入容器。缺少字体时，文字模板可能使用替代字体或生成失败。

### Python 引擎配置

引擎的下载源、额外模板目录、GIF 限制等由其独立的 `config.toml` 处理。本插件将它保存在插件数据目录下的 `python-engine/engine-data/config/config.toml`，通常完整位置为 `data/plugin_data/astrbot_plugin_memes/python-engine/engine-data/config/config.toml`；缓存和数据也保存在相邻的专用目录中。使用已有引擎 Python 路径时，仍使用这份插件专用配置。

例如在配置文件中可用 `[resource]` 的 `resource_url` 指定单一资源地址，或用 `resource_urls` 配置多个候选地址；`[meme]` 的 `meme_dirs` 用于加载额外模板目录，`[gif]` 的 `gif_max_size`、`gif_max_frames` 控制 GIF 输出。修改后重启 AstrBot。字段定义见[引擎配置源码](https://github.com/MemeCrafters/meme-generator/blob/main/meme_generator/config.py)和[配置说明](https://github.com/MemeCrafters/meme-generator/wiki/配置文件)。本插件将目录定位到专用环境，其余字段沿用上游定义。

## 命令

默认不需要命令前缀，直接发送下面的指令。模板名可使用关键词或唯一的模板 key。

| 命令 | 用途 |
| --- | --- |
| `表情指令`、`表情面板`、`表情菜单` | 查看图片帮助面板 |
| `表情列表 [页码]`、`表情包制作 [页码]` | 每页列出 30 个当前可用模板 |
| `表情搜索 关键词` | 搜索关键词与标签 |
| `表情详情 模板` | 查看图片、文字数量及选项，发送预览 |
| `表情帮助 模板`、`表情示例 模板` | 查看模板说明及预览；单独 `表情帮助` 显示总帮助 |
| `模板 图片/文字/选项` | 制作指定表情，支持引擎自带的快捷指令 |
| `随机表情 图片/文字` | 按输入数量选择一个可用模板 |
| `禁用表情 模板`、`启用表情 模板` | 修改当前群聊或私聊的开关，需要管理员权限 |
| `全局禁用表情 模板`、`全局启用表情 模板` | 切换模板的全局默认模式，仅 AstrBot 管理员可用 |
| `表情调用统计 [模板]` | 查看当前会话的调用次数排名 |
| `我的表情调用统计 [模板]` | 查看当前会话内自己的调用次数 |
| `全局表情调用统计 [模板]` | 查看本插件记录的全部会话统计 |
| `我的全局表情调用统计 [模板]` | 查看自己的跨会话统计 |
| `今日表情调用统计`、`本周表情调用统计`、`本月表情调用统计`、`本年表情调用统计` | 按时间段筛选，也可加上“我的”“全局”前缀及模板参数 |
| `图片操作`、`图片工具` | 查看图片和 GIF 工具 |
| `取消表情` | 取消自己在当前会话中的补充输入等待 |

相同关键词对应多个模板时，插件会提示可选 key。日期统计使用 AstrBot 所在服务器的本地时区，周统计从星期一开始。

### 输入图片与文字

- 图片可以随命令发送，也可以引用含图片的消息；引用消息中的文字也会作为输入参与处理。
- OneBot v11 QQ 可通过真实 `@`、文本 `@QQ号` 或 `自己` 获取头像。默认在模板需要两张图而只提供一张时，补入发送者头像作为第一张图。
- 其他平台请直接发送或引用图片。若该适配器提供发送者头像 URL，也可使用 `自己`；跨用户头像查询与群管理员识别仅针对 OneBot QQ 实现。
- 每段文字用空白分隔，含空格时使用英文单引号或双引号，例如 `"今天 也要开心"`。`""` 表示空文字，反斜线保持原样。
- `#名字` 按出现顺序为图片设置名称；名字有空格时可写为 `"#小明 同学"`。
- 输入选项前先查看 `表情详情 模板`，使用实际列出的短选项、长选项或别名。支持 `--选项 值`、`--选项=值`、`-短选项 值`。
- 无参数选项按 Python 模板定义的动作设置值，例如 `圆` 会启用圆形头像；布尔选项也支持 `--选项=true` 或 `--选项=false`。重复指定同一选项时，最后一次为准。
- `--` 结束选项解析。需要把 `--开头` 当作文字时，将其放在 `--` 后面。

例如在 OneBot QQ 中，可发送 `petpet 自己`；使用真实图片时，发送 `petpet` 并附上一张图片。其他模板请按详情要求传入对应数量的图片和文字。

```text
表情详情 petpet
表情列表 2
表情搜索 摸
今日表情调用统计 petpet
```

### 开关与权限

当前会话的开关按平台、机器人账户、群聊／私聊隔离。AstrBot 管理员可以管理所有会话；启用 `allow_group_admin` 时，OneBot QQ 的群主和管理员也可以修改本群开关，但不能操作全局开关。

全局启用表示默认允许，仅拒绝黑名单会话；全局禁用表示默认拒绝，仅允许白名单会话。切换模式会保留各自的黑名单和白名单，因此切回原模式时会恢复该模式下的会话设置。配置项 `disabled_memes` 优先于聊天命令开关，被它禁用的模板需要先从配置中移除，才能重新使用。

### 图片工具

支持水平／竖直翻转、左翻／右翻／上翻／下翻、旋转、缩放、裁剪、灰度图、黑白、反相／反色、横向／纵向拼接，以及 GIF 分解、合成、倒放和变速。具体参数见 `图片操作`：

```text
缩放 50% [附图片或引用图片]
旋转 90 [附图片或引用图片]
gif变速 2x [附 GIF 或引用 GIF]
```

方括号内是操作说明，不需要发送这些字。GIF 分解按 `max_output_images` 限制逐张发送；超过上限会提示，不会自动打包 ZIP 或合并转发。

## 配置

在 AstrBot WebUI 的本插件配置页修改。以下默认值与 `_conf_schema.json` 对应。

| 配置项 | 默认值 | 说明 |
| --- | --- | --- |
| `engine_python_executable` | `""` | 留空时自动建立独立引擎环境；也可填写已安装 `meme-generator==0.1.14` 的独立环境 Python 绝对路径 |
| `command_prefixes` | `[""]` | 默认无需前缀，直接发送指令 |
| `disabled_memes` | `[]` | 永久禁用的模板 key，优先于聊天开关 |
| `check_resources_on_startup` | `true` | 启动后在后台检查并下载 Python 引擎模板图片；字体另行安装 |
| `use_sender_when_no_image` | `false` | 需要一张图但未提供时尝试使用发送者头像 |
| `use_sender_for_two_images` | `true` | 需要两张图但只提供一张时，在前面补发送者头像 |
| `use_default_when_no_text` | `false` | 需要文字但没有文字输入时使用模板默认文字 |
| `random_show_info` | `true` | 随机制作前显示模板名称 |
| `allow_group_admin` | `true` | 允许 OneBot QQ 群主和管理员修改本群开关 |
| `params_mismatch_policy.too_few_image` | `"prompt"` | 图片不足：`prompt` 提示，`get` 等待补充，`ignore` 忽略 |
| `params_mismatch_policy.too_few_text` | `"prompt"` | 文字不足：同上 |
| `params_mismatch_policy.too_much_image` | `"prompt"` | 图片过多：`prompt` 提示，`drop` 丢弃超出部分，`ignore` 忽略 |
| `params_mismatch_policy.too_much_text` | `"prompt"` | 文字过多：同上 |
| `input_timeout` | `60` | `get` 模式每次等待补充的秒数，实际限制为 5～300 |
| `download_timeout` | `30` | 下载图片的超时秒数 |
| `max_image_size_mb` | `20` | 单张输入图片的大小上限，MB |
| `max_concurrent_jobs` | `2` | 并发生成任务数，实际限制为 1～8 |
| `max_output_images` | `10` | 图片工具单次最多发送的图片数，实际限制为 1～50 |

将 `too_few_image` 或 `too_few_text` 设为 `get` 后，插件会提示继续发送所需输入。等待仅属于当前会话中的当前用户，支持直接补图或文字；即使配置了必须使用 `/` 前缀，也可以不带前缀补充输入。发送 `取消表情` 可取消，超时后等待自动失效，插件重载或重启也会清除等待。

## 与 NoneBot 版本的差异

- 使用 AstrBot 的插件生命周期、消息组件和权限接口，不需要安装 NoneBot 或其 ORM、等待器插件。Python 引擎自身声明的绘图库和参数解析依赖由 pip 安装。
- 表情列表使用分页图片卡片；调用统计显示前 20 个模板的条形图。帮助、搜索、详情和工具说明也使用图片面板，模板详情另发送引擎预览图。
- GIF 分解逐张发送图片，并设发送上限；不提供 ZIP 下载和合并转发。
- OneBot QQ 之外的平台以直接／引用图片为主；头像获取和群管理员能力受上述适配范围限制。
- 开关与调用统计存入 AstrBot 插件数据目录的 `memes.sqlite3`。不会自动导入 NoneBot 的设置文件或历史数据库。
- 原版的引擎选项和快捷指令由运行时模板元数据读取；模板集合随安装的引擎版本而定，不在本插件中复制一份绘图代码。

## 开发与检查

在插件目录中运行：

```bash
python -m unittest discover -s tests -v
```

解析和持久化状态的测试可以独立运行；消息路由回归测试使用接口替身，绘图测试在独立引擎环境中使用真实的 `meme-generator==0.1.14`，选取无需下载外部素材的模板。设置环境变量 `MEMES_TEST_PYTHON` 为独立引擎解释器的路径，还可从宿主环境运行跨进程测试；设置 `MEMES_TEST_RUNTIME` 可指定测试配置保存目录。缺少引擎依赖或未指定解释器时，相应集成测试会跳过。

可选安装 `ruff` 后执行 `ruff check .`。本版的实际检查结果与验证范围见 [VALIDATION.md](VALIDATION.md)。部署后仍应在目标平台验证附图、引用、权限和 GIF 发送。

## 来源与许可

本项目保留上游 MIT 许可与 `Copyright (c) 2021 MeetWq`，详见 [LICENSE](LICENSE) 和 [NOTICE.md](NOTICE.md)。表情资源与字体由生成引擎处理，需遵守相应资源自己的许可。

- [NoneBot 插件源码](https://github.com/MemeCrafters/nonebot-plugin-memes)
- [Python 生成器源码](https://github.com/MemeCrafters/meme-generator)
- [AstrBot 官方插件开发指南](https://docs.astrbot.app/dev/star/plugin-new.html)
- [AstrBot SDK 源码](https://github.com/AstrBotDevs/AstrBot)
