void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  float opacityCorrection = opacityReference/(samples*oversampling);
  vec3 delta = rayDir/(samples*oversampling);

  vec3 currentPoint = entryPoint;
  do {
    float reconstruction = reconstructValue(currentPoint);
    processPoint(reconstruction, opacityCorrection);
    if (result.a > alphaThreshold) return;
    currentPoint += delta;
  } while (inBounds(currentPoint));
}
