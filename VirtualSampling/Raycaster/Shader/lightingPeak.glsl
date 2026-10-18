void main() {
  result = vec4(0.0);
  vec3 rayDir = normalize(entryPoint - cameraPosInTextureSpace);

  float samples = dot(abs(rayDir), voxelCount);
  float opacityCorrection = opacityReference / (samples * oversampling);
  vec3 delta = rayDir / (samples * oversampling);

  vec3 nextPos = entryPoint;
  vec4 next = reconstructGradientValue(nextPos);

  do {
    vec3 currentPos = nextPos;
    vec4 current = next;

    nextPos = currentPos + delta;
    next = reconstructGradientValue(nextPos);

    float peakValue = fetchPeakValue(current.w, next.w);
    vec3 peakGradient;
    bool insertedPeak = false;

    if (peakValue > 0.0) {
      float f0 = current.w - peakValue;
      float f1 = next.w - peakValue;

      if (f0 * f1 < 0.0) {
        vec3 p0 = currentPos; float g0 = f0;
        vec3 p1 = nextPos;    float g1 = f1;

        for (int k = 0; k < 2; ++k) {
          float denom = (g1 - g0);
          if (abs(denom) < 1e-8) break;

          vec3 prt = p0 - (p1 - p0) * (g0 / denom);
          vec4 rec = reconstructGradientValue(prt);

          float frt = rec.w - peakValue;
          peakGradient = rec.xyz;

          if (frt * g0 < 0.0) { p1 = prt; g1 = frt; }
          else                { p0 = prt; g0 = frt; }
        }

        float denom = (g1 - g0);
        if (abs(denom) > 1e-8) {
          vec3 hitPos = p0 - (p1 - p0) * (g0 / denom);

          // Split the step's opacity across the two subsegments
          float segLen = length(nextPos - currentPos);
          float tHit = (segLen > 0.0) ? clamp(length(hitPos - currentPos) / segLen, 0.0, 1.0) : 0.0;

          processPoint(peakValue, peakGradient, hitPos, opacityCorrection * tHit);
          if (result.a > alphaThreshold) return;
          processPoint(next.w, next.xyz, nextPos, opacityCorrection * (1.0 - tHit));
          if (result.a > alphaThreshold) return;

          insertedPeak = true;
        }
      }
    }

    // If no peak was inserted, do the
    // normal single sample for the full step
    if (!insertedPeak) {
      processPoint(next.w, next.xyz, nextPos, opacityCorrection);
      if (result.a > alphaThreshold) return;
    }

  } while (inBounds(nextPos));
}
