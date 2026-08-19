# Contributing

欢迎提交 Issue 和 Pull Request。涉及匹配规则时，请同时提供匿名化的正例、反例文件名，并新增自动化测试，避免提高召回率时引入明显误删风险。

开发检查：

```bash
PYTHONPATH=src python -m unittest discover -s tests -v
python -m py_compile src/media_dup_finder/*.py
```

安全相关修改必须遵守：扫描阶段只读；禁止默认永久删除；禁止自动执行智能建议；同组必须至少保留一个文件。

