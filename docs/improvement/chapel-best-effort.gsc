# 教堂最好结果（best-effort非完全分离）
# 实测结论：高斯 0.24/0.03 + 体素单位裁剪中间 50% + ortho，
# 教堂核心最清晰但球壳去不掉。0.25/0.75 之类的归一化裁剪是错的
# （setclipbounds 取体素单位那样写只剩 0.5 体素宽）。
# 直接 Run 可复现。详见 02-dataset-facts.md §7 与 09 决策记录。
setvolume 8
setmethod 0
setrate 1
setsubdiv 1
constantSampleCount false
setuseortho true
setalphathreshold 0.99
setLevel 0
settfparams true 0.24 0.03
setclipbounds 61.5 184.5 61.5 184.5 55.25 165.75
resetrotation
addrotationx -20
addrotationy 35
