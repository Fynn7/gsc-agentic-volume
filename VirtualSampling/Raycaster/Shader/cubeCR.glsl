float reconstructValue(vec3 position) {
  position = position + 0.5/voxelCount;
  float centerSample = texture(volume, position).r;

  vec3 delta = 1.0/voxelCount;
  vec3 sample0, sample1;
  sample0.x = texture(volume,
                      vec3(position.x - delta.x, position.y, position.z)).r;
  sample1.x = texture(volume,
                      vec3(position.x + delta.x, position.y, position.z)).r;
  sample0.y = texture(volume,
                      vec3(position.x, position.y - delta.y, position.z)).r;
  sample1.y = texture(volume,
                      vec3(position.x, position.y + delta.y, position.z)).r;
  sample0.z = texture(volume,
                      vec3(position.x, position.y, position.z - delta.z)).r;
  sample1.z = texture(volume,
                      vec3(position.x, position.y, position.z + delta.z)).r;
  vec3 scaledPosition = position * voxelCount - 0.5;
  vec3 fraction = scaledPosition - floor(scaledPosition);
  vec3 correctionPolynomial = (fraction * (fraction - 1.0)) / 2.0;
  centerSample += dot((sample0 - centerSample * 2.0 + sample1),
                      correctionPolynomial);
  return centerSample;
}

vec4 reconstructGradientValue(vec3 position) {
  position = position + 0.5/voxelCount;
  float centerSample = texture(volume, position).r;

  vec3 delta = 1.0/voxelCount;
  vec3 sample0, sample1;
  sample0.x = texture(volume,
                      vec3(position.x - delta.x, position.y, position.z)).r;
  sample1.x = texture(volume,
                      vec3(position.x + delta.x, position.y, position.z)).r;
  sample0.y = texture(volume,
                      vec3(position.x, position.y - delta.y, position.z)).r;
  sample1.y = texture(volume,
                      vec3(position.x, position.y + delta.y, position.z)).r;
  sample0.z = texture(volume,
                      vec3(position.x, position.y, position.z - delta.z)).r;
  sample1.z = texture(volume,
                      vec3(position.x, position.y, position.z + delta.z)).r;
  vec3 scaledPosition = position * voxelCount - 0.5;
  vec3 fraction = scaledPosition - floor(scaledPosition);
  vec3 correctionPolynomial = (fraction * (fraction - 1.0)) / 2.0;
  centerSample += dot((sample0 - centerSample * 2.0 + sample1),
                      correctionPolynomial);
  return vec4((sample1 - sample0)/2.0, centerSample);
}
