- schema_version: 1
- issue_id: KI-20260829-001
- title: lechao_lcview_hal_device 设备节点类型名残留（HAL 退役后命名未同步）
- discovered_in: eaaa7c811aa9
- origin: pre-existing
- severity: P2
- blocking: False
- blocking_reason: 
- status: fixed
- task: lcview-refactor
- resolved_in: 701fd8213408

## body

## 问题现场
- HAL 退役（CDP 0cc5eb08831b 直读内核 + 本批清理 9056a45555e6 删除 hal 二进制/rc/VINTF）后，
  内核设备节点 /dev/vendor_lechao_lcview 仍使用 lechao_lcview_hal_device 类型名：
  - code/rpi5/aosp/new/device/brcm/rpi5/sepolicy/lechao_lcview.te: type lechao_lcview_hal_device, dev_type, file_type;
  - code/rpi5/aosp/modified/device/brcm/rpi5/sepolicy/file_contexts.diff: /dev/vendor_lechao_lcview 打该标签
- 纯命名残留：hal 已退役但类型名带 hal 后缀，与 lcview daemon 域（lechao_lcview）命名不一致。
- 功能无影响：daemon 直读内核链路已完整跑通（liveness/守恒/吞吐验证 pass）。

## 修法描述
- 类型重命名 lechao_lcview_hal_device → lechao_lcview_device（或 lechao_lcview_dev_node）：
  1) lechao_lcview.te 类型定义改名；2) file_contexts.diff 设备节点标签同步；
  3) 重新编译 sepolicy（precompiled_sepolicy）并上板；4) 重启后 /dev 节点按新 label 重新打标。
- 收益低（纯命名）、风险中（sepolicy 重编 + 设备节点重打标），暂缓执行，作为候选 cleanup。
