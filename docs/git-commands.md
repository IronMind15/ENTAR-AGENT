# Git 常用命令速查

> 恩特小助手项目实战记录，按使用场景分类

---

## 🔍 查看状态

```bash
git status                     # 看当前工作区状态（改了哪些文件）
git log --oneline              # 看提交历史（一行一个）
git log --oneline -20          # 只看最近 20 条
git log --oneline v1.0..v1.1   # 看两个版本之间的提交
git diff                       # 看工作区未暂存的改动
git diff --stat                # 只看改了哪些文件，不展示内容
git diff v1.0..v1.1            # 看两个版本之间的全部代码差异
git diff v1.0..v1.1 --stat     # 看两个版本之间改动了哪些文件
git show v1.1                  # 查看某个标签/提交的详情
git remote -v                  # 查看远程仓库地址
```

## 📥 暂存与提交

```bash
git add 文件名                  # 暂存单个文件
git add .                      # 暂存所有改动
git add -A                     # 暂存所有改动（含删除）
git commit -m "说明"            # 提交
git commit --amend --no-edit   # 补到上一个提交（不改说明文字）
git commit --amend -m "新说明"  # 补到上一个提交（顺便改说明）
```

## 📤 推送与同步

```bash
git push                       # 推送到远程（需已建立追踪）
git push origin master         # 推 master 分支到远程
git push --force origin master # 强推（amend 改过历史后需要用）
git push origin v1.1           # 推送单个标签到远程
git push origin --tags         # 推送所有本地标签到远程
git push --delete origin v1.1  # 删除远程标签
```

## 🏷️ 标签管理

```bash
git tag -a v1.1 -m "版本说明"   # 打注释标签（推荐）
git tag                        # 列出所有本地标签
git tag -d v1.1                # 删除本地标签
git ls-remote --tags origin    # 查看远程有哪些标签
```

## ⏪ 回滚与撤销

```bash
git restore --staged 文件名     # 撤销 git add（取消暂存）
git reset --soft HEAD~1        # 撤回上一个 commit，代码保留
git reset --hard HEAD~1        # 撤回上一个 commit，代码也回去
git revert 那个commitID         # 安全回滚（生成反向提交，不丢历史）
```

## 🌱 分支

```bash
git branch                     # 看本地分支列表
git branch 分支名               # 创建新分支
git checkout 分支名             # 切换分支
git checkout -b 分支名          # 创建并切换
git merge 分支名                # 合并分支到当前
git branch -d 分支名            # 删除本地分支
```

## 🆕 初始化与远程

```bash
git init                       # 初始化本地仓库
git clone 仓库地址              # 从远程克隆到本地
git remote add origin 地址      # 关联远程仓库
git push -u origin master      # 首次推送，建立追踪关系
```

---

## 实战流程 (恩特小助手发版)

```
# 1. 看状态
git status
git log --oneline v1.0..HEAD     # 确认有哪些新提交

# 2. 更新文档
# 编辑 CHANGELOG.md
# 编辑 README.md
git add CHANGELOG.md README.md
git commit -m "📝 更新版本日志和说明"

# 3. 打标签
git tag -a v1.2 -m "版本说明"

# 4. 推送
git push origin master --tags

# 5. 如果漏了文件想补
git add 漏掉的文件
git commit --amend --no-edit
git push --force origin master --tags
```

---

## 小贴士

| 场景 | 正确做法 |
|------|---------|
| 改了 CHANGELOG 想补进上一个提交 | `git add CHANGELOG.md && git commit --amend --no-edit && git push --force origin master --tags` |
| 标签打错位置了 | `git tag -d v1.1` 删本地 → `git push --delete origin v1.1` 删远程 → 重新打 |
| 想对比两个版本 | `git diff v1.0..v1.1 --stat` |
| 忘了上次提交了啥 | `git log --oneline -5` |
