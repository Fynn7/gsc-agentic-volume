#include "VolumeGenerator.h"

namespace VolumeGenerator {

Volume<uint8_t> marschnerLobb(size_t width,
                              size_t height,
                              size_t depth,
                              float alpha,
                              float fM) {
  Volume<uint8_t> v{
    VolumeMetaData{
      width, height, depth,
      {1, 1, 1}
    }
  };

  constexpr float pi = 3.14159265358979323846f;

  auto indexToCoord = [](size_t i, size_t N) -> float {
    if (N <= 1) return 0.0f;
    return (-1.0f + 2.0f * float(i) / float(N - 1)) * float(N) / float(N + 1);
  };

  size_t idx = 0;
  for (size_t z = 0; z < depth; ++z) {
    float zc = indexToCoord(z, depth);
    for (size_t y = 0; y < height; ++y) {
      float yc = indexToCoord(y, height);
      for (size_t x = 0; x < width; ++x) {
        float xc = indexToCoord(x, width);

        float r = std::sqrt(xc * xc + yc * yc);
        float rho_r = std::cos(2.0f * pi * fM * std::cos(pi * r * 0.5f));

        float numerator = 1.0f - std::sin(pi * zc * 0.5f) + alpha * (1.0f + rho_r);
        float denominator = 2.0f * (1.0f + alpha);
        float rho = numerator / denominator;

        if (rho < 0.0f) rho = 0.0f;
        else if (rho > 1.0f) rho = 1.0f;

        float scaled = rho * 255.0f;
        if (scaled < 0.0f) scaled = 0.0f;
        else if (scaled > 255.0f) scaled = 255.0f;

        v.data[idx++] = static_cast<uint8_t>(std::lround(scaled));
      }
    }
  }

  return v;
}

Volume<uint8_t> linear(size_t width, size_t height, size_t depth) {
  Volume<uint8_t> v{
    VolumeMetaData{
      width, height, depth,
      {1, 1, 1}
    }
  };

  size_t i = 0;
  for (size_t z = 0; z < depth; ++z) {
    for (size_t y = 0; y < height; ++y) {
      for (size_t x = 0; x < width; ++x) {
        v.data[i++] = uint8_t((z * 255) / (depth - 1));
      }
    }
  }

  return v;
}

Volume<uint8_t> ramp(size_t width, size_t height, size_t depth) {
  Volume<uint8_t> v{
    VolumeMetaData{
      width, height, depth,
      {1, 1, 1}
    }
  };

  size_t i = 0;
  for (size_t z = 0; z < depth; ++z) {
    for (size_t y = 0; y < height; ++y) {
      for (size_t x = 0; x < width; ++x) {
        v.data[i++] = uint8_t(((z + x) * 255) / (depth - 1 + width - 1));
      }
    }
  }

  return v;
}

Volume<uint8_t> sphere(size_t width, size_t height, size_t depth) {
  Volume<uint8_t> v{
    VolumeMetaData{
      width, height, depth,
      {1, 1, 1}
    }
  };

  const Vec3 center{
    float(width - 1) / 2.0f,
    float(height - 1) / 2.0f,
    float(depth - 1) / 2.0f
  };
  const float maxDist = center.length();

  size_t i = 0;
  for (size_t z = 0; z < depth; ++z) {
    for (size_t y = 0; y < height; ++y) {
      for (size_t x = 0; x < width; ++x) {
        v.data[i++] = 255 - uint8_t(255 * (Vec3(float(x), float(y), float(z)) - center).length() / maxDist);
      }
    }
  }

  return v;
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
