# 科研记录

本项目是“科研记录”的可维护源码版，使用 PySide6 构建，数据保存在本地 JSON 文件中。

## 功能

- 今日学习打卡、任务、每日心得
- 四象限任务管理，支持添加、完成、删除
- 图谱库，支持论文题目、DOI/链接、标签、备注、图片路径
- 统计分析，包括日历、学习趋势、任务完成比例、选中日期详情
- 中文颜色主题设置，支持各区域颜色和卡片/输入框透明度

## 运行

```powershell
python -m venv .venv
.\.venv\Scripts\python -m pip install -r requirements.txt
.\.venv\Scripts\python -m research_record
```

默认运行目录为 `E:\科研记录`。首次启动会自动创建新的 `config.json` 和 `数据\kris_data.json`。

## 打包

```powershell
.\scripts\build.ps1
```

打包产物位于 `dist\科研记录.exe`。脚本会先备份 `E:\科研记录` 中的旧配置、数据、assets 和旧 exe，再复制新的 exe。
