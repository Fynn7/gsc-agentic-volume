void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  float opacityCorrection = opacityReference/(samples*oversampling*float(subdiv+1));
  vec3 delta = rayDir/(samples*oversampling);

  float stepSize = length(delta*voxelCount);
  vec3 virtualDelta = delta/float(subdiv+1);

  vec3 currentPos = entryPoint;
  vec4 current = reconstructGradientValue(currentPos);

  do {
    processPoint(current.w, current.xyz, currentPos, opacityCorrection);

    vec3 nextPos = currentPos + delta;
    vec4 next = reconstructGradientValue(nextPos);

    vec4 hermiteData = vec4(
                            current.w,
                            dot(current.xyz, rayDir) * stepSize,
                            next.w,
                            dot(next.xyz, rayDir) * stepSize
                            );
    vec4 polynomial = hermiteData * hermiteBaseT;

    for (int i = 0;i<subdiv;++i) {
      currentPos += virtualDelta;
      float tau = float(i+1)/float(subdiv+1);
      vec4 tauPowers = vec4(1.0,tau, tau*tau, tau*tau*tau);
      float virtualHermiteSample = dot(polynomial, tauPowers);
      vec3 linearGradient = mix(current.xyz,next.xyz,tau);
      processPoint(virtualHermiteSample, linearGradient, currentPos, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

    currentPos = nextPos;
    current = next;
  } while (inBounds(currentPos));
}
