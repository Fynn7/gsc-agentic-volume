void helper(vec3 position, out vec3 g0,
            out vec4 sample0, out vec4 sample1) {
  position = position * voxelCount;
  vec3 beta = position - round(position);

  g0 = 0.5f - beta;
  vec3 d0 = 0.25f + 0.5f*beta;

  vec3 position0 = position - d0;
  vec3 position1 = position0 + 0.5;

  sample0 = vec4(sampleVolume(position0.x, position0.y, position0.z),
                 sampleVolume(position0.x, position1.y, position0.z),
                 sampleVolume(position0.x, position0.y, position1.z),
                 sampleVolume(position0.x, position1.y, position1.z));
  sample1 = vec4(sampleVolume(position1.x, position0.y, position0.z),
                 sampleVolume(position1.x, position1.y, position0.z),
                 sampleVolume(position1.x, position0.y, position1.z),
                 sampleVolume(position1.x, position1.y, position1.z));
}

float reconstructValue(vec3 position) {
  vec3 g0;
  vec4 sample0, sample1;
  helper(position, g0, sample0, sample1);
  vec4 xInterpolated = mix(sample1, sample0, g0.x);
  vec2 yInterpolated = mix(xInterpolated.yw, xInterpolated.xz, g0.y);
  return mix(yInterpolated.y, yInterpolated.x, g0.z);
}

vec4 reconstructGradientValue(vec3 position) {
  vec3 g0;
  vec4 sample0, sample1;
  helper(position, g0, sample0, sample1);

  vec4 xInterpolated = mix(sample1, sample0, g0.x);
  vec4 xGradient = 2.0 * (sample1 - sample0);
  vec4 yInterpolated = mix(vec4(xInterpolated.yw, xGradient.yw),
                           vec4(xInterpolated.xz, xGradient.xz), g0.y);
  vec2 yGradient = 2.0 * (xInterpolated.yw - xInterpolated.xz);
  vec3 zInterpolated = mix(vec3(yInterpolated.yw, yGradient.y),
                           vec3(yInterpolated.xz, yGradient.x), g0.z);
  float zGradient = 2.0 * (yInterpolated.y - yInterpolated.x);

  return vec4(vec3(zInterpolated.y, zInterpolated.z, zGradient),
              zInterpolated.x);
}
