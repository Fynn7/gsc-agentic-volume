#include "QVis.h"

#include "Compression.h"

#include <fstream>
#include <algorithm>
#include <filesystem>
#include <sstream>
#include <cctype>
#include <cstring>


class QVisDatLine {
public:
  QVisDatLine(const std::string input);

  std::string id;
  std::string value;

private:
  void ltrim(std::string& s);
  void rtrim(std::string& s);
  void trim(std::string& s);
};

QVis::QVis(const std::string& filename) {
  load(filename);
}

void QVis::load(const std::string& filename) {
  std::ifstream datfile(filename);
  if (!datfile) throw QVisFileException{std::string("Unable to read file ") + filename};

  uint32_t bytesPerVoxel{0};
  std::string compression{"none"};

  std::filesystem::path p{filename};

  std::string rawFilename;
  std::string line;
  while (std::getline(datfile, line)) {
    QVisDatLine l{line};

    if (l.id == "objectfilename") {
      if (p.parent_path().string().empty())
        rawFilename = l.value;
      else
        rawFilename = p.parent_path().string() + "/" + l.value;
    } else if (l.id == "resolution") {
      std::vector<std::string> t = tokenize(l.value);
      if (t.size() != 3) throw QVisFileException{"invalid resolution tag"};
      int w,h,d;
      try {
        w = stoi(t[0]);
        h = stoi(t[1]);
        d = stoi(t[2]);
      } catch (const std::exception&) {
        throw QVisFileException{"invalid resolution tag"};
      }
      if (w <= 0 || h <= 0 || d <= 0) {
        throw QVisFileException{"resolution tag to small"};
      }
      volume.metaData.width  = size_t(w);
      volume.metaData.height = size_t(h);
      volume.metaData.depth  = size_t(d);
    }
    else if (l.id == "slicethickness") {
      std::vector<std::string> t = tokenize(l.value);
      if (t.size() != 3) throw QVisFileException{"invalid slicethickness tag"};
      try {
        volume.metaData.scale = Vec3{stof(t[0]), stof(t[1]), stof(t[2])};
        volume.metaData.normalizeScale();
      } catch (const std::invalid_argument&) {
        throw QVisFileException{"invalid slicethickness tag"};
      }
    }
    else if (l.id == "format") {
      if (l.value == "char" ||
          l.value == "uchar" ||
          l.value == "byte") {
        bytesPerVoxel = 1;
      } else {
        // if it's not 8bit, we assume 16bit
        bytesPerVoxel = 2;
      }
    }
    else if (l.id == "endianess") {
      if (l.value != "little") {
        throw QVisFileException{"only little endian data supported by this mini-reader"};
      }
    }
    else if (l.id == "compression") {
      if (l.value.empty() || l.value == "none") {
        compression = "none";
      } else if (l.value == "lz4") {
        compression = "lz4";
      } else {
        throw QVisFileException{"unsupported compression tag"};
      }
    }
  }

  datfile.close();

  if (rawFilename.empty())
    throw QVisFileException{"object filename not found"};

  if (volume.metaData.volume() == 0)
    throw QVisFileException{"object filename not found"};

  std::ifstream rawFile(rawFilename, std::ios::binary);
  if (!rawFile)
    throw QVisFileException{"unable to open raw file"};

  rawFile.seekg(0, std::ios::end);
  if (!rawFile) {
    throw QVisFileException{"Failed to compute length of " + rawFilename};
  }
  std::streampos endPos = rawFile.tellg();
  if (endPos < 0) {
    throw QVisFileException{"Failed to compute length of " + rawFilename};
  }
  const uint64_t fileSize = static_cast<std::uint64_t>(endPos);

  const uint64_t expectedRawBytes = volume.metaData.volume() * uint64_t(bytesPerVoxel);
  if (compression == "none") {
    if (fileSize < expectedRawBytes) {
      throw QVisFileException{"File too small. Git lfs not installed?"};
    }
  }

  rawFile.seekg(0, std::ios::beg);

  std::vector<uint8_t> rawBytes;
  rawBytes.resize(static_cast<size_t>(fileSize));
  if (fileSize > 0) {
    rawFile.read(reinterpret_cast<char*>(rawBytes.data()), std::streamsize(fileSize));
    if (!rawFile) {
      throw QVisFileException{"Failed to read raw file"};
    }
  }
  rawFile.close();

  if (compression == "lz4") {
    try {
      rawBytes = Compression::decompressLZ4(rawBytes);
    } catch (const Compression::CompressionError& e) {
      throw QVisFileException{std::string("LZ4 decompression failed: ") + e.what()};
    }
  }

  if (rawBytes.size() < expectedRawBytes) {
    throw QVisFileException{"Raw payload too small for declared resolution/format"};
  }

  if (bytesPerVoxel != 1) {
    std::vector<uint16_t> data(volume.metaData.volume());
    std::memcpy(data.data(), rawBytes.data(), static_cast<size_t>(expectedRawBytes));

    uint16_t minVal = data[0];
    uint16_t maxVal = data[0];
    for (size_t i = 1; i < data.size(); ++i) {
      const uint16_t v = data[i];
      if (v < minVal) minVal = v;
      if (v > maxVal) maxVal = v;
    }

    const uint32_t range = uint32_t(maxVal) - uint32_t(minVal);

    volume.data.resize(volume.metaData.volume());
    if (range == 0u) {
      std::fill(volume.data.begin(), volume.data.end(), uint8_t(0));
    } else {
      for (size_t i = 0; i < data.size(); ++i) {
        const uint32_t v = uint32_t(data[i]) - uint32_t(minVal);
        volume.data[i] = uint8_t((v * 255u + range / 2u) / range);
      }
    }
  } else {
    volume.data.assign(rawBytes.begin(),
                       rawBytes.begin() + static_cast<std::ptrdiff_t>(expectedRawBytes));
  }
}

void QVis::save(const std::string& filename, bool compression) {
  save(volume, filename, compression);
}

void QVis::save(const Volume<uint8_t>& volume,
                const std::string& filename,
                bool compression) {
  std::filesystem::path datPath{filename};
  std::filesystem::path payloadName = datPath.stem();
  payloadName += (compression ? ".lz4" : ".raw");
  std::filesystem::path rawPath = datPath.parent_path() / payloadName;

  std::ofstream datfile(filename);
  if (!datfile) {
    throw QVisFileException{std::string("Unable to write file ") + filename};
  }

  datfile << "ObjectFileName: " << payloadName.filename().string() << "\n";
  datfile << "Resolution: "
  << volume.metaData.width  << " "
  << volume.metaData.height << " "
  << volume.metaData.depth  << "\n";
  datfile << "SliceThickness: "
  << volume.metaData.scale.x << " "
  << volume.metaData.scale.y << " "
  << volume.metaData.scale.z << "\n";

  if (compression) {
    datfile << "Compression: lz4\n";
  }

  // This mini-writer always writes 8-bit little-endian data
  datfile << "Format: uchar\n";
  datfile << "Endianess: little\n";
  datfile.close();

  std::ofstream rawFile(rawPath, std::ios::binary);
  if (!rawFile) {
    throw QVisFileException{
      std::string("Unable to write raw data file ") + rawPath.string()
    };
  }

  if (!volume.data.empty()) {
    if (compression) {
      std::vector<uint8_t> compressed;
      try {
        compressed = Compression::compressLZ4(volume.data);
      } catch (const Compression::CompressionError& e) {
        throw QVisFileException{std::string("LZ4 compression failed: ") + e.what()};
      }
      rawFile.write(reinterpret_cast<const char*>(compressed.data()),
                    std::streamsize(compressed.size()));
    } else {
      rawFile.write(reinterpret_cast<const char*>(volume.data.data()),
                    std::streamsize(volume.data.size()));
    }
  }

  rawFile.close();
}

std::vector<std::string> QVis::tokenize(const std::string& str) const {
  std::vector<std::string> strElements;
  std::string buf;
  std::stringstream ss(str);
  while (ss >> buf) strElements.push_back(buf);
  return strElements;
}

QVisDatLine::QVisDatLine(const std::string input) {
  std::size_t found = input.find_first_of(":");
  if (found == std::string::npos) return;

  id = input.substr(0, found);
  value = input.substr(found + 1);

  trim(id);
  trim(value);

  transform(id.begin(), id.end(), id.begin(), tolower);
  if (id != "objectfilename")
    transform(value.begin(), value.end(), value.begin(), tolower);
}

void QVisDatLine::ltrim(std::string& s) {
  s.erase(s.begin(), std::find_if(s.begin(), s.end(), [](unsigned char ch) {
    return !std::isspace(ch);
  }));
}

void QVisDatLine::rtrim(std::string& s) {
  s.erase(std::find_if(s.rbegin(), s.rend(), [](unsigned char ch) {
    return !std::isspace(ch);
  }).base(), s.end());
}

void QVisDatLine::trim(std::string& s) {
  ltrim(s);
  rtrim(s);
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
