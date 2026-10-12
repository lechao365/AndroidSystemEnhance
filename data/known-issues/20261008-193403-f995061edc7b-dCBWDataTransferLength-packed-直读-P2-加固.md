- schema_version: 1
- issue_id: KI-20261008-002
- title: bot.c 对 packed CBW 结构体 dCBWDataTransferLength 字段多处直接取址直读（未走 le32 移位）
- discovered_in: f995061edc7b
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: fixed
- task: usb-fault-inject
- resolved_in: dev-e0b6b624da78
- archived_in: 
- kind: 

## body

- 现场: bot.c 中 CBW 为 packed 结构体（字节序按 USB 小端 wire），dCBWDataTransferLength 被多处直接读参与逻辑：cbw.dCBWDataTransferLength 出现在 bot.c:132（日志）、:168/:173/:193/:199/:200（scsi 解析与 to_send 截断）、:256/:261/:262/:287/:310（OUT 接收与残差计算）等，均为 packed 直读。R-18 方向 4 只把 CBW 签名校验与 CSW 构造改为 msd_le32_get/put 移位式读写，未覆盖 dCBWDataTransferLength 读取点。
- 缺陷归属: KIR-001 回退验证——packed 直读为既有代码，非本批引入，本批未触碰这些行；属加固建议非缺陷（当前 RPI5 目标机小端 + 实际 wire 字节序恰好一致，无实际故障路径），按准入场景表「加固建议非缺陷」登记。
- 影响: 若移植到大端目标或依赖结构体对齐布局的场合，packed 直读会读出错误字节序；当前目标无实际故障，防御性加固。
- 修法方向: 对 dCBWDataTransferLength 读取点统一改走 msd_le32_get(&cbw.dCBWDataTransferLength)，与 CBW 签名/CSW 构造保持一致的移位式读写风格。
- 闭环: 已闭环（代码层面已满足，无需改动）。核查确认 bot.c 中 dCBWDataTransferLength 唯一读取点 bot.c:137 已走 `msd_le32_get((const uint8_t *)&cbw.dCBWDataTransferLength)`，后续全部经 cbw_dlen 使用；其余 CBW/CSW 多字节字段（dCBWSignature bot.c:113/:115、dCBWTag bot.c:136、dCSWSignature/dCSWTag/dCSWDataResidue bot.c:315-317/:325/:329）亦全部走 msd_le32_get/put，无 packed 直读点。其余仅 bmCBWFlags/bCBWLUN/bCBWCBLength/CBWCB[] 单字节/字节数组访问，无字节序问题。修法已在历史提交 aa6a2c4 落地，本批仅闭环登记。本批 CDP 批次修复，提交后由 promote 归档回填正式 commit。
