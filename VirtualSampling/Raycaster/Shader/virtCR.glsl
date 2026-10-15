void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  float opacityCorrection = opacityReference/(samples*oversampling*float(subdiv+1));
  vec3 delta = rayDir/(samples*oversampling);

  vec3 currentPos = entryPoint;
  vec4 reconstructions;
  reconstructions.x = reconstructValue(currentPos);
  reconstructions.y = reconstructions.x;
  currentPos += delta;
  reconstructions.z = reconstructValue(currentPos);

  do {
    processPoint(reconstructions.y, opacityCorrection);

    currentPos += delta;
    reconstructions.w = reconstructValue(currentPos);
    vec4 polynomial = reconstructions * catmullRomBaseT;
    for (int i = 0;i<subdiv;++i) {
      float tau = float(i+1)/float(subdiv+1);
      vec4 tauPowers = vec4(1.0, tau, tau*tau, tau*tau*tau);
      float virtualCatmullRomSample = dot(polynomial, tauPowers);
      processPoint(virtualCatmullRomSample, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

    reconstructions.xyz = reconstructions.yzw;
  } while (inBounds(currentPos-delta));
}
