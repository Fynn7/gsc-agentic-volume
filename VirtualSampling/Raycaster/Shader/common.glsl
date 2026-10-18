uniform sampler3D volume;
uniform sampler1D transfer;

uniform mat4 modelView;
uniform mat4 modelViewIT;

uniform vec3 minBounds;
uniform vec3 maxBounds;
uniform vec3 voxelCount;
uniform vec2 tfSize;
uniform float oversampling;
uniform vec3 cameraPosInTextureSpace;
uniform float alphaThreshold;

uniform int subdiv;

in vec3 entryPoint;
out vec4 result;

const mat4 catmullRomBaseT = mat4(
                               0.0,  1.0,  0.0,  0.0,
                               -0.5,  0.0,  0.5,  0.0,
                               1.0, -2.5,  2.0, -0.5,
                               -0.5,  1.5, -1.5,  0.5
                               );

const mat4 hermiteBaseT = mat4(
                               1.0,  0.0,  0.0,  0.0,
                               0.0,  1.0,  0.0,  0.0,
                               -3.0, -2.0,  3.0, -1.0,
                               2.0,  1.0, -2.0,  1.0
                               );

const mat4 cubicBSplineBaseT = mat4(
                                    1.0/6.0f, 4.0/6.0f, 1.0/6.0f, 0.0/6.0f,
                                    -3.0/6.0f, 0.0/6.0f, 3.0/6.0f, 0.0/6.0f,
                                    3.0/6.0f,-6.0/6.0f, 3.0/6.0f, 0.0/6.0f,
                                    -1.0/6.0f, 3.0/6.0f,-3.0/6.0f, 1.0/6.0f
                                    );

const mat4 cubicBSplineDerT = mat4(
                                   0.0/6.0f, 0.0/6.0f, 0.0/6.0f, 0.0/6.0f,
                                   -3.0/6.0f, 0.0/6.0f, 3.0/6.0f, 0.0/6.0f,
                                   6.0/6.0f,-12.0/6.0f, 6.0/6.0f, 0.0/6.0f,
                                   -3.0/6.0f, 9.0/6.0f,-9.0/6.0f, 3.0/6.0f
                                   );

const float opacityReference = 200.0;

vec4 transferFunction(float v) {
  return texture(transfer, v);
}

float sampleVolume(vec3 pos) {
  return texture(volume, (pos+0.5)/voxelCount).r;
}

float sampleVolume(float x, float y, float z) {
  return sampleVolume(vec3(x,y,z));
}

vec4 under(vec4 current, vec4 last) {
  last.rgb = last.rgb + (1.0-last.a) * current.a * current.rgb;
  last.a   = last.a   + (1.0-last.a) * current.a;
  return last;
}

bool inBounds(vec3 pos) {
  return pos.x >= minBounds.x && pos.y >= minBounds.y && pos.z >= minBounds.z &&
  pos.x <= maxBounds.x && pos.y <= maxBounds.y && pos.z <= maxBounds.z;
}

vec3 lighting(vec3 vPosition, vec3 vNormal, vec3 color) {
  vec3 vLightAmbient  = vec3(0.1,0.1,0.1);
  vec3 vLightDiffuse  = vec3(0.5,0.5,0.5);
  vec3 vLightSpecular = vec3(0.8,0.8,0.8);
  vec3 lightDir       = vec3(0.0,0.0,1.0);

  vec3 vViewDir    = normalize(vPosition-vec3(0.0,0.0,0.0));

  vec3 vReflection = normalize(reflect(vViewDir, vNormal));
  return clamp(color*vLightAmbient+
               color*vLightDiffuse*max(abs(dot(vNormal, lightDir)),0.0)+
               vLightSpecular*pow(max(dot(vReflection, lightDir),0.0),8.0), 0.0,1.0);
}

void processPoint(float v, float opacityCorrection) {
  vec4 classified = transferFunction(v);
  classified.a = 1.0 - pow(1.0 - classified.a, opacityCorrection);
  result = under(classified, result);
}

vec3 safeNormalize(vec3 v) {
  float l = length(v);
  return (l == 0.0) ? v : v/l;
}

void processPoint(float v, vec3 gradient, vec3 position,
                  float opacityCorrection) {
  vec4 classified = transferFunction(v);
  vec3 normal = safeNormalize(gradient);
  classified.rgb = lighting((modelView*vec4(position-0.5,1.0)).xyz,
                         (modelViewIT*vec4(normal,0.0)).xyz, classified.rgb);
  classified.a = 1.0 - pow(1.0 - classified.a, opacityCorrection);
  result = under(classified, result);
}

// ********************** Pre-Integration *******************************

uniform sampler2D transferPre;

vec4 underPremultipliedColor(vec4 current, vec4 last) {
  last.rgb = last.rgb + (1.0-last.a) * current.rgb;
  last.a   = last.a   + (1.0-last.a) * current.a;
  return last;
}

void processPointPre(float currentV, float nextV) {
  vec4 v = texture(transferPre, vec2(currentV,nextV));
  result = underPremultipliedColor(v, result);
}

// ************************ Peak Finding ********************************

uniform sampler2D peakTable;

int scalarToBin(float s) {
  float x = clamp(s, 0.0, 1.0) * float(tfSize - 1.0);
  return int(floor(x + 0.5));
}

float fetchPeakValue(float s0, float s1) {
  int x = scalarToBin(s0);
  int y = scalarToBin(s1);

  float r = texture(peakTable, (vec2(x, y) + 0.5) / vec2(tfSize)).r;
  //float r = texelFetch(peakTable, ivec2(x, y), 0.0).r;
  if (r >= 1.0) return -1.0;

  return clamp(r * (255.0 / 254.0), 0.0, 1.0);
}
