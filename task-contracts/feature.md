# 任务契约:功能实现(feature)

把本文件 copy 到项目里填写。coordinator 读取后映射为 `feature-impl.js` 的 `args.contract`。
缺任一必填字段,workflow 的 precheck 会 fail-closed 拒跑。

## 必填字段(contract)

- `summary`:要实现的功能,一句话可验证的描述。
- `validation_command`:**独立黑盒测试套件**的运行命令(本模板不自写测试,这套件来自 test 模板或既有,
  当作 spec)。例:`python -m pytest tests/test_feature.py -q`。
- `boundaries`:允许改动的范围(文件/目录/模块);其余一律不碰。

## 可选字段

- `minimal_diff`:`{ max_files: <int>, max_lines: <int> }`。bug-fix 模式建议收紧,防 scope creep。
- `acceptance`:额外的验收点(自然语言),供 cross-review 对照。

## 由 coordinator 填写(config)

- `skillDir`:curryflows skill 安装路径(scripts 所在)。
- `projectDir`:项目仓路径。
- `worktree`:本 thread 的独立 worktree 路径。
- `branch`:本 thread 的分支名(`curryflows/feature/<thread-id>`)。
- `maxRounds`(默认 3)、`codexEffort`(默认 medium)。

## 示例(YAML/JSON 任选,coordinator 转成对象)

```yaml
summary: "average() 对空列表返回 0 而不是抛 ZeroDivisionError"
validation_command: "python -m pytest tests/test_average.py -q"
boundaries: "只改 src/stats.py;不动 tests/"
minimal_diff: { max_files: 2, max_lines: 40 }
```

## 修 bug 的用法

bug 修复 = 先用 test 模板加一个 RED 复现用例(reproduce-first,由 coordinator 强制:没有 RED 用例
不准进 feature),再用本模板拟合到绿,`minimal_diff` 收紧。
