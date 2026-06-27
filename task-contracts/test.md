# 任务契约:构建测试(test)

copy 到项目填写。coordinator 映射为 `test-gen.js` 的 `args.contract`。缺必填字段 precheck fail-closed。

本模板产出**独立黑盒测试套件**,成为 feature / perf 模板的验证 oracle。测公共契约/可观察行为,
不读实现内部、不 over-fit。

## 必填字段(contract)

- `summary`:要补测的目标,一句话。
- `test_command`:测试套件运行命令(带覆盖率更好)。
- `coverage_target`:覆盖率目标(数值或描述)。
- `gap_scope`:要闭合的缺口范围(模块/行为)。
- `boundaries`:允许改动范围(应只含测试文件;不改被测代码)。

## 硬门(模板强制)

- 新测试在**当前(假定正确)代码上必须 PASS**(validate)。
- **有效性负控硬停**:在隔离 worktree 里注入一个故障,**至少一个新测试必须 FAIL**,
  证明测试非恒真(`aTestFailed==true` 才允许 accept)。
- cross-review 查:黑盒性、缺口闭合、恒真断言、over-fit。

## loop

repair 阶段只**精修现有测试**,不重新生成。

## 示例

```yaml
summary: "为 stats 模块的边界输入补黑盒测试"
test_command: "python -m pytest tests/ --cov=src/stats -q"
coverage_target: "src/stats 行覆盖 >= 90%"
gap_scope: "src/stats 的空输入、单元素、超大输入路径"
boundaries: "只新增/改 tests/;不动 src/"
```

## 与其它模板的关系

修 bug 时先用本模板加一个 RED 复现用例(reproduce-first),再交 feature 模板拟合到绿。
