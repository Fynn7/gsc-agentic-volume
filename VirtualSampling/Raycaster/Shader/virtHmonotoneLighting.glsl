vec4 forceMonotone(float p0, float m0, float p1, float m1) {
  float secant = p1 - p0;
  float eps = 1e-8f;

  if (abs(secant) < eps) { // zero case
    m0 = 0.0;
    m1 = 0.0;
    return vec4(p0, m0, p1, m1);
  }

  if (secant > 0.0f) { // secant is positive
    m0 = max(0.0, m0);
    m1 = max(0.0, m1);
  } else { // secant is negative
    m1 = min(0.0, m1);
    m0 = min(0.0, m0);
  }

  // Hyman Limiter
  float alpha = m0 / secant;
  float beta = m1 / secant;
  float hypothenuse = alpha * alpha + beta * beta;

  if (hypothenuse > 9.0 ) {
    float tau = 3.0 / sqrt(hypothenuse);
    m0 = tau * alpha * secant;
    m1 = tau * beta * secant;
  }
  return vec4(p0, m0, p1, m1);
}

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

    vec4 hermiteData = forceMonotone(
                            current.w,
                            dot(current.xyz, rayDir) * stepSize,
                            next.w,
                            dot(next.xyz, rayDir) * stepSize
                            );
    vec4 polynomial = hermiteData * hermiteBaseT;

    for (int i = 0;i<subdiv;++i) {
      currentPos += virtualDelta;
      float tau = float(i+1)/float(subdiv+1);
      vec4 tauPowers = vec4(1.0, tau, tau*tau, tau*tau*tau);
      float virtualHermiteSample = dot(polynomial, tauPowers);
      vec3 linearGradient = mix(current.xyz,next.xyz,tau);
      processPoint(virtualHermiteSample, linearGradient, currentPos, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

    currentPos = nextPos;
    current = next;
  } while (inBounds(currentPos));
}
