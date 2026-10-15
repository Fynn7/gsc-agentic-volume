#include "VolumeTransformer.h"

#include <utility>

namespace {

float initCausalCoeffFloat(const std::vector<float>& c, size_t n, float pole, float lambda) {
  const size_t horizon = (n < 12) ? n : 12;
  float zk = pole;
  float sum = c[0];
  for (size_t k = 1; k < horizon; ++k) {
    sum += zk * c[k];
    zk *= pole;
  }
  return sum;
}

float initAntiCausalCoeffFloat(const std::vector<float>& c, size_t n, float pole, float lambda) {
  return (pole / (pole - 1.0f)) * c[n - 1];
}

void convertToBSplineCoefficientsFloat(std::vector<float>& c, size_t n, float pole, float lambda) {
  if (n == 1) return;

  c[0] = lambda * initCausalCoeffFloat(c, n, pole, lambda);
  for (size_t k = 1; k < n; ++k) {
    c[k] = lambda * c[k] + pole * c[k - 1];
  }

  c[n - 1] = initAntiCausalCoeffFloat(c, n, pole, lambda);
  for (size_t k = n - 1; k-- > 0;) {
    c[k] = pole * (c[k + 1] - c[k]);
  }
}

void apply1DPrefilterFloat(std::vector<float>& data,
                           size_t baseIndex,
                           size_t anzahl,
                           size_t step,
                           float pole,
                           float lambda,
                           std::vector<float>& line) {
  if (anzahl <= 1) return;

  for (size_t i = 0; i < anzahl; ++i) {
    line[i] = data[baseIndex + i * step];
  }

  convertToBSplineCoefficientsFloat(line, anzahl, pole, lambda);

  for (size_t i = 0; i < anzahl; ++i) {
    data[baseIndex + i * step] = line[i];
  }
}

void prefilterVolumeBSplineFloat(Volume<float>& inputVolume, float pole, float lambda) {
  std::vector<float>& data = inputVolume.data;
  const size_t width = inputVolume.metaData.width;
  const size_t height = inputVolume.metaData.height;
  const size_t depth = inputVolume.metaData.depth;

  std::vector<float> line;
  line.resize(std::max({width, height, depth}));

  for (size_t z = 0; z < depth; ++z) {
    for (size_t y = 0; y < height; ++y) {
      size_t baseIndex = y * width + z * width * height;
      apply1DPrefilterFloat(data, baseIndex, width, 1, pole, lambda, line);
    }
  }

  for (size_t z = 0; z < depth; ++z) {
    for (size_t x = 0; x < width; ++x) {
      size_t baseIndex = x + z * width * height;
      apply1DPrefilterFloat(data, baseIndex, height, width, pole, lambda, line);
    }
  }

  for (size_t y = 0; y < height; ++y) {
    for (size_t x = 0; x < width; ++x) {
      size_t baseIndex = x + y * width;
      apply1DPrefilterFloat(data, baseIndex, depth, width * height, pole, lambda, line);
    }
  }
}

void computeCubicWeights(float t, float w[4]) {
  float t2 = t * t;
  float t3 = t2 * t;
  const float inv6 = 1.0f / 6.0f;
  w[0] = (1.0f - t) * (1.0f - t) * (1.0f - t) * inv6;
  w[1] = (3.0f * t3 - 6.0f * t2 + 4.0f) * inv6;
  w[2] = (-3.0f * t3 + 3.0f * t2 + 3.0f * t + 1.0f) * inv6;
  w[3] = t3 * inv6;
}

std::pair<int, float> coordSeparation(float x) {
  int i = int(std::floor(x));
  return {i, x - float(i)};
}

float reconstructCubicBS(const Volume<float>& volume, float x, float y, float z) {
  const int width = int(volume.metaData.width);
  const int height = int(volume.metaData.height);
  const int depth = int(volume.metaData.depth);

  auto [i, tx] = coordSeparation(x);
  auto [j, ty] = coordSeparation(y);
  auto [k, tz] = coordSeparation(z);

  float wx[4], wy[4], wz[4];
  computeCubicWeights(tx, wx);
  computeCubicWeights(ty, wy);
  computeCubicWeights(tz, wz);

  float result = 0.0f;

  for (int dz = 0; dz < 4; dz++) {
    int iz = std::min(std::max(k - 1 + dz, 0), depth - 1);
    for (int dy = 0; dy < 4; dy++) {
      int iy = std::min(std::max(j - 1 + dy, 0), height - 1);
      for (int dx = 0; dx < 4; dx++) {
        int ix = std::min(std::max(i - 1 + dx, 0), width - 1);
        size_t index = size_t(ix) + size_t(iy * width) + size_t(iz * width * height);
        const float c = volume.data[index];
        const float w = wx[dx] * wy[dy] * wz[dz];
        result += c * w;
      }
    }
  }

  return result;
}

void computeQuadraticWeights(float t, float w[3]) {
  float t2 = t * t;

  w[0] = 0.5f * (t - 0.5f) * (t - 0.5f);
  w[1] = 0.5f * 0.5f * 3 - t2;
  w[2] = 0.5f * (t + 0.5f) * (t + 0.5f);
}

float reconstructQuadBS(const Volume<float>& volume, float x, float y, float z) {
  const int width = int(volume.metaData.width);
  const int height = int(volume.metaData.height);
  const int depth = int(volume.metaData.depth);

  auto [i, tx] = coordSeparation(x);
  auto [j, ty] = coordSeparation(y);
  auto [k, tz] = coordSeparation(z);

  float wx[3], wy[3], wz[3];
  computeQuadraticWeights(tx, wx);
  computeQuadraticWeights(ty, wy);
  computeQuadraticWeights(tz, wz);

  float result = 0.0f;

  for (int dz = 0; dz < 3; dz++) {
    int iz = std::min(std::max(k - 1 + dz, 0), depth - 1);
    for (int dy = 0; dy < 3; dy++) {
      int iy = std::min(std::max(j - 1 + dy, 0), height - 1);
      for (int dx = 0; dx < 3; dx++) {
        int ix = std::min(std::max(i - 1 + dx, 0), width - 1);
        size_t index = size_t(ix) + size_t(iy * width) + size_t(iz * width * height);

        const float c = volume.data[index];
        const float w = wx[dx] * wy[dy] * wz[dz];
        result += c * w;
      }
    }
  }

  return result;
}

}

namespace VolumeTransformer {

Volume<float> prefilterCubicBSpline(const Volume<uint8_t>& inputVolume) {
  Volume<float> floatData = inputVolume.convert<float>();
  const float pole = std::sqrt(3.0f) - 2.0f;
  const float lambda = (1.0f - pole) * (1.0f - (1.0f / pole));
  prefilterVolumeBSplineFloat(floatData, pole, lambda);
  return floatData;
}

Volume<float> prefilterQuadraticBSpline(const Volume<uint8_t>& inputVolume) {
  Volume<float> floatData = inputVolume.convert<float>();
  const float pole = 2.0f * std::sqrt(2.0f) - 3.0f;
  const float lambda = (1.0f - pole) * (1.0f - (1.0f / pole));
  prefilterVolumeBSplineFloat(floatData, pole, lambda);
  return floatData;
}

bool testPrefilterCubic(const Volume<uint8_t>& inputVolume,
                        const Volume<float>& prefilteredVolume,
                        const float maxError,
                        const size_t border) {
  const size_t w = inputVolume.metaData.width;
  const size_t h = inputVolume.metaData.height;
  const size_t d = inputVolume.metaData.depth;

  for (size_t z = border; z < d - border; ++z) {
    for (size_t y = border; y < h - border; ++y) {
      for (size_t x = border; x < w - border; ++x) {
        const float recValue = reconstructCubicBS(prefilteredVolume,
                                                  float(x),
                                                  float(y),
                                                  float(z));
        const float inputValue = inputVolume.getValue(x, y, z) / 255.0f;
        if (fabs(recValue - inputValue) > maxError) {
          return false;
        }
      }
    }
  }

  return true;
}

bool testPrefilterQuad(const Volume<uint8_t>& inputVolume,
                       const Volume<float>& prefilteredVolume,
                       const float maxError,
                       const size_t border) {
  const size_t w = inputVolume.metaData.width;
  const size_t h = inputVolume.metaData.height;
  const size_t d = inputVolume.metaData.depth;

  for (size_t z = border; z < d - border; ++z) {
    for (size_t y = border; y < h - border; ++y) {
      for (size_t x = border; x < w - border; ++x) {
        const float recValue = reconstructQuadBS(prefilteredVolume,
                                                 float(x),
                                                 float(y),
                                                 float(z));
        const float inputValue = inputVolume.getValue(x, y, z) / 255.0f;
        if (fabs(recValue - inputValue) > maxError) {
          return false;
        }
      }
    }
  }

  return true;
}

}

/*
 Copyright (c) 2026 Computer Graphics and Visualization Group, University of
 Duisburg-Essen

 Permission is hereby granted, free of charge, to any person obtaining a copy of
 this software and associated documentation files (the "Software"), to deal in the
 Software without restriction, including without limitation the rights to use, copy,
 modify, merge, publish, distribute, sublicense, and/or sell copies of the Software, and
 to permit persons to whom the Software is furnished to do so, subject to the following
 conditions:

 The above copyright notice and this permission notice shall be included in all copies
 or substantial portions of the Software.

 THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR IMPLIED,
 INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY, FITNESS FOR A
 PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE AUTHORS OR COPYRIGHT
 HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER LIABILITY, WHETHER IN AN ACTION OF
 CONTRACT, TORT OR OTHERWISE, ARISING FROM, OUT OF OR IN CONNECTION WITH THE SOFTWARE OR
 THE USE OR OTHER DEALINGS IN THE SOFTWARE.
 */
