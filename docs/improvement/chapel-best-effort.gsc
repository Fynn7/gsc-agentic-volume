# present246x246x221 教堂最好结果（best-effort，非完全分离）
# 实测结论：Gaussian 0.24/0.03 + 体素单位裁剪保留中间 ~50%，教堂最清晰，
# 但球壳去不掉（见 docs/improvement/02 §7 与 09 决策记录）。直接 Run 即可复现。
setvolume 8
setmethod 0
setrate 1
setsubdiv 1
constantSampleCount false
setuseortho false
setalphathreshold 0.99
setLevel 0
settfparams true 0.24 0.03
setclipbounds 61.5 184.5 61.5 184.5 44.2 176.8
resetrotation
addrotationx -20
addrotationy 35
