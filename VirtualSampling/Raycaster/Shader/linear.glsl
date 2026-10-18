float reconstructValue(vec3 normalizedPosition) {
  return texture(volume, normalizedPosition+0.5/voxelCount).r;
}

vec4 reconstructGradientValue(vec3 normalizedPosition) {
  float volumeValue = reconstructValue(normalizedPosition);

  vec3 d = 1.0/voxelCount;
  vec3 sample0 = vec3(
                      reconstructValue(vec3(normalizedPosition.x-d.x,
                                            normalizedPosition.y,
                                            normalizedPosition.z)),
                      reconstructValue(vec3(normalizedPosition.x,
                                            normalizedPosition.y-d.y,
                                            normalizedPosition.z)),
                      reconstructValue(vec3(normalizedPosition.x,
                                            normalizedPosition.y,
                                            normalizedPosition.z-d.z))
                      );
  vec3 sample1 = vec3(
                      reconstructValue(vec3(normalizedPosition.x+d.x,
                                            normalizedPosition.y,
                                            normalizedPosition.z)),
                      reconstructValue(vec3(normalizedPosition.x,
                                            normalizedPosition.y+d.y,
                                            normalizedPosition.z)),
                      reconstructValue(vec3(normalizedPosition.x,
                                            normalizedPosition.y,
                                            normalizedPosition.z+d.z))
                      );
  return vec4((sample1 - sample0)/2.0,volumeValue);
}
