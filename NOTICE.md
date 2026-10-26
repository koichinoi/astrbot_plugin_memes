# 来源及修改说明

本 AstrBot 移植版依据 [MemeCrafters/nonebot-plugin-memes](https://github.com/MemeCrafters/nonebot-plugin-memes) `0.8.1`，提交 [`836d4414c280a0c133018df2b311be4d153fe97d`](https://github.com/MemeCrafters/nonebot-plugin-memes/commit/836d4414c280a0c133018df2b311be4d153fe97d) 的功能与接口实现。

上游使用 MIT 许可，版权声明为：

```text
Copyright (c) 2021 MeetWq
```

完整的上游许可保留于本目录的 [LICENSE](LICENSE)。本移植改写了框架适配层，替换为 AstrBot 的消息事件、插件配置与权限机制，使用 SQLite 保存会话开关和调用记录，并调整了列表、统计和图片工具的输出形式。黑名单／白名单模式、参数处理和生成逻辑参考上游行为。

本版实际绘图由 [MemeCrafters/meme-generator](https://github.com/MemeCrafters/meme-generator) Python 项目提供，依赖固定为 `meme-generator==0.1.14`。本移植重新适配了 Python 引擎的模板对象、参数模型、预览、资源下载和图片处理接口，并通过本地工作进程隔离引擎与 AstrBot 的依赖环境。该引擎的 MIT 许可及其资源、字体的许可由相应上游项目维护；此插件不将下载的资源和字体纳入自身许可。

本移植不是 NoneBot、MemeCrafters 或 AstrBot 官方发布的插件，不代表上述项目的认可。AstrBot 兼容接口参考其[官方文档](https://docs.astrbot.app/dev/star/plugin-new.html)和[SDK 源码](https://github.com/AstrBotDevs/AstrBot)。
