void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  float opacityCorrection = opacityReference/(samples*oversampling*float(subdiv+1));
  vec3 delta = rayDir/(samples*oversampling);

  vec3 currentPos = entryPoint;
  float current = reconstructValue(currentPos);

  do {
    processPoint(current, opacityCorrection);

    vec3 nextPos = currentPos + delta;
    float next = reconstructValue(nextPos);
    for (int i = 0;i<subdiv;++i) {
      float tau = float(i+1)/float(subdiv+1);
      float virtualLinearSample = mix(current, next, tau);
      processPoint(virtualLinearSample, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

    currentPos = nextPos;
    current = next;
  } while (inBounds(currentPos));
}
