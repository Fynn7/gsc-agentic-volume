void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint-cameraPosInTextureSpace);

  float samples = dot(abs(rayDir),voxelCount);
  float opacityCorrection = opacityReference/(samples*oversampling*float(subdiv+1));
  vec3 delta = rayDir/(samples*oversampling);
  vec3 virtualDelta = delta/float(subdiv+1);

  vec3 currentPos = entryPoint;
  vec4 reconstruction0 = reconstructGradientValue(currentPos);
  vec4 reconstruction1 = reconstruction0;
  vec3 currentPos1 = currentPos;
  currentPos += delta;
  vec4 reconstruction2 = reconstructGradientValue(currentPos);
  vec4 reconstruction3;

  do {
    processPoint(reconstruction1.w, reconstruction1.xyz, currentPos1, opacityCorrection);

    currentPos += delta;
    reconstruction3 = reconstructGradientValue(currentPos);

    vec4 catmullRomData[4] = vec4[4](
      vec4(reconstruction0.x,reconstruction1.x,reconstruction2.x,reconstruction3.x),
      vec4(reconstruction0.y,reconstruction1.y,reconstruction2.y,reconstruction3.y),
      vec4(reconstruction0.z,reconstruction1.z,reconstruction2.z,reconstruction3.z),
      vec4(reconstruction0.w,reconstruction1.w,reconstruction2.w,reconstruction3.w)
    );

    vec4 polynomial[4] = vec4[4](
      catmullRomData[0] * catmullRomBaseT,
      catmullRomData[1] * catmullRomBaseT,
      catmullRomData[2] * catmullRomBaseT,
      catmullRomData[3] * catmullRomBaseT
    );

    vec3 virtualPos = currentPos1;
    for (int i = 0;i<subdiv;++i) {
      virtualPos += virtualDelta;
      float tau = float(i+1)/float(subdiv+1);
      vec4 tauPowers = vec4(1.0,tau, tau*tau, tau*tau*tau);
      vec3 virtualCatmullRomGradient = vec3(
                                  dot(polynomial[0], tauPowers),
                                  dot(polynomial[1], tauPowers),
                                  dot(polynomial[2], tauPowers)
                                );
      float virtualCatmullRomSample = dot(polynomial[3], tauPowers);
      processPoint(virtualCatmullRomSample, virtualCatmullRomGradient, virtualPos, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

    reconstruction0 = reconstruction1;
    reconstruction1 = reconstruction2;
    reconstruction2 = reconstruction3;
    currentPos1 += delta;
  } while (inBounds(currentPos-delta));
}
