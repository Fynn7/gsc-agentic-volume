float reconstructSH(vec3 i, vec4 wx, vec4 wy, vec4 wz) {
  vec3 g0 = vec3(wx.x + wx.y, wy.x + wy.y, wz.x + wz.y);
  vec3 g1 = vec3(wx.z + wx.w, wy.z + wy.w, wz.z + wz.w);
  vec3 a0 = vec3(wx.y / g0.x, wy.y / g0.y, wz.y / g0.z);
  vec3 a1 = vec3(wx.w / g1.x, wy.w / g1.y, wz.w / g1.z);

  vec3 position0 = i - 1.0f + a0;
  vec3 position1 = i + 1.0f + a1;

  vec4 sample0 = vec4(sampleVolume(position0.x, position0.y, position0.z),
                      sampleVolume(position0.x, position1.y, position0.z),
                      sampleVolume(position0.x, position0.y, position1.z),
                      sampleVolume(position0.x, position1.y, position1.z));
  vec4 sample1 = vec4(sampleVolume(position1.x, position0.y, position0.z),
                      sampleVolume(position1.x, position1.y, position0.z),
                      sampleVolume(position1.x, position0.y, position1.z),
                      sampleVolume(position1.x, position1.y, position1.z));

  vec4 xInterpolated = sample1 * g1.x + sample0 * g0.x;
  vec2 yInterpolated = xInterpolated.yw * g1.y + xInterpolated.xz * g0.y;
  return yInterpolated.y * g1.z + yInterpolated.x * g0.z;
}

float reconstructValue(vec3 pos) {
  pos = pos*voxelCount;
  vec3 i = vec3(ivec3(pos));
  vec3 t = pos - i;

  vec4 wx = cubicBSplineBaseT * vec4(1.0,t.x, t.x*t.x, t.x*t.x*t.x);
  vec4 wy = cubicBSplineBaseT * vec4(1.0,t.y, t.y*t.y, t.y*t.y*t.y);
  vec4 wz = cubicBSplineBaseT * vec4(1.0,t.z, t.z*t.z, t.z*t.z*t.z);

  return reconstructSH(i, wx, wy, wz);
}

vec4 reconstructGradientValue(vec3 pos) {
  pos = pos*voxelCount;
  vec3 i = vec3(ivec3(pos));
  vec3 t = pos - i;

  vec4 wx = cubicBSplineBaseT * vec4(1.0,t.x, t.x*t.x, t.x*t.x*t.x);
  vec4 wy = cubicBSplineBaseT * vec4(1.0,t.y, t.y*t.y, t.y*t.y*t.y);
  vec4 wz = cubicBSplineBaseT * vec4(1.0,t.z, t.z*t.z, t.z*t.z*t.z);

  vec4 wxDer = cubicBSplineDerT * vec4(0.0, 1.0, t.x, t.x*t.x);
  vec4 wyDer = cubicBSplineDerT * vec4(0.0, 1.0, t.y, t.y*t.y);
  vec4 wzDer = cubicBSplineDerT * vec4(0.0, 1.0, t.z, t.z*t.z);

  return vec4(reconstructSH(i, wxDer, wy, wz),
              reconstructSH(i, wx, wyDer, wz),
              reconstructSH(i, wx, wy, wzDer),
              reconstructSH(i, wx, wy, wz));
}
