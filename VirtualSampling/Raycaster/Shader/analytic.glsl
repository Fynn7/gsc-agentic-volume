uniform int volumeIndex;

float computeSphere(vec3 pos) {
  return 1.0-length(pos)/1.7320508;
}

vec3 computeSphereGrad(vec3 pos) {
  return -(1.0 / 1.7320508) * normalize(pos);
}

float computeRamp(vec3 pos) {
  pos = (pos+1.0)/2.0;
  return (pos.z+pos.x)/2.0;
}

vec3 computeRampGrad(vec3 pos) {
  return vec3(0.5, 0.0, 0.5);
}

float computeML(vec3 pos) {
  const float alpha   = 0.25;
  const float fM      = 6.0;
  const float pi = 3.1415926535;

  float r = sqrt(pos.x * pos.x + pos.y * pos.y);
  float rho_r = cos(2.0 * pi * fM * cos(pi * r * 0.5));
  float numerator   = 1.0 - sin(pi * pos.z * 0.5) +
  alpha * (1.0f + rho_r);
  float denom = 2.0 * (1.0 + alpha);

  return clamp(numerator / denom, 0.0, 1.0);
}

vec3 computeMLGrad(vec3 pos) {
  const float alpha = 0.25;
  const float fM    = 6.0;
  const float pi    = 3.1415926535;

  float r = sqrt(pos.x*pos.x + pos.y*pos.y);
  float denom = 2.0 * (1.0 + alpha);

  float halfPiR = pi * 0.5 * r;
  float cosHalf = cos(halfPiR);
  float sinHalf = sin(halfPiR);

  float A     = 2.0 * pi * fM * cosHalf;
  float dRhoR_dr = (pi*pi * fM) * sin(A) * sinHalf;
  float invR = (r > 1e-8) ? (1.0 / r) : 0.0;
  float dr_dx = pos.x * invR;
  float dr_dy = pos.y * invR;
  float dRhoR_dx = dRhoR_dr * dr_dx;
  float dRhoR_dy = dRhoR_dr * dr_dy;
  float scaleXY = alpha / denom;
  float dRho_dx = scaleXY * dRhoR_dx;
  float dRho_dy = scaleXY * dRhoR_dy;
  float dRho_dz = (-(pi * 0.5) * cos(pi * 0.5 * pos.z)) / denom;

  return vec3(dRho_dx, dRho_dy, dRho_dz);
}

float reconstructValue(vec3 position) {
  vec3 normPos = 2.0*((position+0.5/voxelCount)-0.5);
  switch (volumeIndex) {
    case 0: return computeSphere(normPos);
    case 1: return computeRamp(normPos);
    case 2: 
    case 3: return computeML(normPos);
    default: return 0.0;
  }
}

vec4 reconstructGradientValue(vec3 position) {
  vec3 normPos = 2.0*((position+0.5/voxelCount)-0.5);
  switch (volumeIndex) {
    case 0: return vec4(computeSphereGrad(normPos),computeSphere(normPos));
    case 1: return vec4(computeRampGrad(normPos),computeRamp(normPos));
    case 2:
    case 3: return vec4(computeMLGrad(normPos),computeML(normPos));
    default: return vec4(0.0,0.0,0.0,0.0);
  }
}
