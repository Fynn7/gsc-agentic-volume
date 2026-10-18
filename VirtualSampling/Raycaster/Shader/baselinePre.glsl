void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  vec3 delta = rayDir/(samples*oversampling);

  vec3 currentPos = entryPoint;
  float current = reconstructValue(currentPos);
  do {
    currentPos += delta;
    float next = reconstructValue(currentPos);
    processPointPre(current, next);
    if (result.a > alphaThreshold) return;
    current = next;
  } while (inBounds(currentPos));
}
