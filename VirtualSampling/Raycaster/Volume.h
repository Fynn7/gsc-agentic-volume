#pragma once

#include <bit>
#include <string>
#include <vector>
#include <array>
#include <sstream>
#include <cmath>
#include <algorithm>
#include <fstream>

#include <type_traits>
#include <limits>
#include <cstdint>

#include <Vec3.h>
#include <half.h>

struct VolumeMetaData {
  VolumeMetaData() :
    width{0}, height{0}, depth{0},
    scale{0.0f, 0.0f, 0.0f} {}

  VolumeMetaData(const size_t width,
                 const size_t height,
                 const size_t depth,
                 const Vec3&   scale) :
  width{width}, height{height}, depth{depth},
  scale{scale} {}


  VolumeMetaData(const VolumeMetaData& other) :
    width{other.width}, height{other.height}, depth{other.depth},
    scale{other.scale}
    {}

  void normalizeScale() {
    const Vec3 extend =
    scale * Vec3{float(width), float(height), float(depth)} / float(maxSize());
    const float m = std::max(extend.x, std::max(extend.y, extend.z));
    scale = scale / m;
  }

  std::string toString() const {
    std::stringstream ss;
    ss << "width: "  << width      << "\n";
    ss << "height: " << height     << "\n";
    ss << "depth: "  << depth      << "\n";
    ss << "scale: "  << scale      << "\n";
    return ss.str();
  }

  size_t volume() const {
    return width*height*depth;
  }

  size_t maxSize() const {
    return std::max({width, height, depth});
  }

  size_t width;
  size_t height;
  size_t depth;
  Vec3   scale;

};


template<typename T = uint8_t>
class Volume {
public:
  Volume() :
  metaData{}
  {}

  Volume(const Volume& other) :
  metaData{other.metaData}, data(other.data)
  {}

  Volume(const VolumeMetaData& metaData) :
  metaData{metaData}, data(metaData.volume())
  {}

  VolumeMetaData metaData;
  std::vector<T> data;

  const std::array<T, 2>& getMinMax() const {
    if (!minMax) {
      if (data.empty()) {
        minMax.emplace(std::array<T,2>{T{}, T{}}); // define your empty semantics
      } else {
        auto [mn, mx] = std::minmax_element(data.begin(), data.end());
        minMax.emplace(std::array<T,2>{*mn, *mx});
      }
    }
    return *minMax;
  }

  std::string toString() const {
    std::stringstream ss;
    ss << metaData.toString();
    ss << "datasize: " << data.size() << "\n";

    for (size_t i = 0; i < data.size(); ++i) {
      if (i > 0 && i % metaData.width == 0)       ss << "\n";
      if (i > 0 && i % (metaData.width * metaData.height) == 0) ss << "\n";
      // Unary + promotes small integer types so uint8_t prints as a number,
      // and leaves floats as floats (promoted to double).
      ss << +data[i] << " ";
    }

    return ss.str();
  }

  size_t possibleMIPLevels() const {
    return static_cast<size_t>(std::min({
      std::countr_zero(metaData.width),
      std::countr_zero(metaData.height),
      std::countr_zero(metaData.depth)
    }) + 1);
  }

  Volume<T> padToPow2(T value = T(0)) const {
    if (metaData.width == 0 || metaData.height == 0 || metaData.depth == 0) {
      return *this;
    }

    if (std::has_single_bit(metaData.width) &&
        std::has_single_bit(metaData.height) &&
        std::has_single_bit(metaData.depth)) {
      return *this;
    }

    const size_t paddedWidth = std::bit_ceil(metaData.width);
    const size_t paddedHeight = std::bit_ceil(metaData.height);
    const size_t paddedDepth = std::bit_ceil(metaData.depth);

    VolumeMetaData paddedMetaData{
      paddedWidth,
      paddedHeight,
      paddedDepth,
      metaData.scale
    };

    Volume<T> padded{paddedMetaData};
    std::fill(padded.data.begin(), padded.data.end(), value);

    const size_t xOffset = (paddedWidth - metaData.width) / 2;
    const size_t yOffset = (paddedHeight - metaData.height) / 2;
    const size_t zOffset = (paddedDepth - metaData.depth) / 2;

    for (size_t z = 0; z < metaData.depth; ++z) {
      for (size_t y = 0; y < metaData.height; ++y) {
        for (size_t x = 0; x < metaData.width; ++x) {
          const size_t srcIndex = x + y * metaData.width + z * metaData.width * metaData.height;
          const size_t dstIndex = (x + xOffset) +
                                  (y + yOffset) * paddedWidth +
                                  (z + zOffset) * paddedWidth * paddedHeight;
          padded.data[dstIndex] = data[srcIndex];
        }
      }
    }

    return padded;
  }

  // 3D indexing accessor
  T getValue(size_t u, size_t v, size_t w) const {
    const size_t index = u + v * metaData.width + w * metaData.width * metaData.height;
    return data[index];
  }

  // 3D indexing accessor with clamping
  T getValue(int u, int v, int w) const {
    const size_t cu = size_t(std::min<size_t>( metaData.width-1,std::max(0,u)));
    const size_t cv = size_t(std::min<size_t>(metaData.height-1,std::max(0,v)));
    const size_t cw = size_t(std::min<size_t>( metaData.depth-1,std::max(0,w)));
    return getValue(cu, cv, cw);
  }

  bool saveText(const std::string& filename) const {
    std::ofstream ofs(filename);
    if (!ofs.is_open()) {
      return false;
    }

    ofs << metaData.toString();

    // Data: one line per width entries, iterating depth-major, then height, then width
    for (size_t w = 0; w < metaData.depth; ++w) {
      for (size_t v = 0; v < metaData.height; ++v) {
        for (size_t u = 0; u < metaData.width; ++u) {
          const size_t index = u + v * metaData.width + w * metaData.width * metaData.height;
          ofs << +data[index];
          if (u + 1 < metaData.width) {
            ofs << ' ';
          }
        }
        ofs << '\n';
      }
    }

    return true;
  }

  template <class S>
  constexpr S clamp(S v, S lo, S hi) const {
    return (v < lo) ? lo : (v > hi) ? hi : v;
  }

  template <class Int>
  constexpr long double int_range_min() const {
    if constexpr (std::is_signed_v<Int>)
      return static_cast<long double>(std::numeric_limits<Int>::min());
    else
      return 0.0L;
  }

  template <class Int>
  constexpr long double int_range_max() const {
    return static_cast<long double>(std::numeric_limits<Int>::max());
  }

  template <class Int>
  constexpr long double int_range_span() const {
    return int_range_max<Int>() - int_range_min<Int>();
  }

  template <class S>
  Volume<S> convert(
                                     long double minValue = 0.0L,
                                     long double maxValue = 1.0L,
                                     bool clampToRange = true,
                                     bool roundToNearest = true
  ) const {
    Volume<S> dst;
    dst.metaData = metaData;
    dst.data.reserve(data.size());

    // Case 1: float -> int (normalized mapping)
    if constexpr (half::is_float_like_v<T> && std::is_integral_v<S>) {
      const long double denom = (maxValue - minValue);

      for (T x : data) {
        long double v = static_cast<long double>(x);

        // Normalize into [0..1] based on (minValue,maxValue)
        long double n = (denom != 0.0L) ? ((v - minValue) / denom) : 0.0L;

        // Map to target integer range
        long double mapped = int_range_min<S>() + n * int_range_span<S>();

        if (clampToRange) {
          mapped = clamp(mapped,
                                 int_range_min<S>(),
                                 int_range_max<S>());
        }

        if (roundToNearest) mapped = (long double)(std::llround(mapped));

        dst.data.push_back(static_cast<S>(mapped));
      }
      return dst;
    }

    // Case 2: int -> float (inverse of the above mapping)
    if constexpr (std::is_integral_v<T> && half::is_float_like_v<S>) {
      const long double span = int_range_span<T>();
      const long double denom = (span != 0.0L) ? span : 1.0L;

      for (T x : data) {
        long double v = static_cast<long double>(x);

        // Normalize integer into [0..1] (or signed range mapped linearly)
        long double n = (v - int_range_min<T>()) / denom;

        // Map into [minValue..maxValue]
        long double out = minValue + n * (maxValue - minValue);

        dst.data.push_back(static_cast<S>(out));
      }
      return dst;
    }

    // Case 3: everything else (including int<->int, float<->float)
    for (T x : data) {
      dst.data.push_back(static_cast<S>(x));
    }
    return dst;
  }

private:
  mutable std::optional<std::array<T, 2>> minMax;


};

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
