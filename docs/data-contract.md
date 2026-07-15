# 公共数据契约 v1

所有 JSON 使用 UTF-8、稳定字段和确定性内容哈希。

## 实体

实体字段包括：`schemaVersion`、`id`、`originRepositoryId`、`subject`、
`title`、`aliases`、`entityType`、`gradeStart`、`gradeEnd`、`domain`、
`summary`、`sourceRefs`、`licenseClass` 和 `contentHash`。

`originRepositoryId` 让公共数据与原三科学科库共享身份；更换 GitHub
仓库地址不会在本地重复创建同一知识。

## 关系

关系字段包括：`schemaVersion`、`id`、`subject`、`fromId`、`toId`、
`relationType`、`reason`、`sourceRefs`、`licenseClass` 和 `contentHash`。

所有关系端点必须存在。关系 ID 全局唯一；先修、支持等方向由 `fromId`
指向 `toId`。

## 哈希和运行包

`contentHash` 是去掉自身哈希字段后，对规范 JSON 计算的 SHA-256。
学科包包含排序后的实体、关系、数量和 `bundleHash`；根 manifest 再记录
每个学科包的路径和哈希。构建不包含当前时间，因此相同源文件始终产生
相同字节。
