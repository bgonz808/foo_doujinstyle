#pragma once

#include <foobar2000/SDK/album_art_helpers.h>
#include <wincodec.h>
#include <shlwapi.h>
#include <algorithm>
#include <span>
#include <vector>

namespace doujin {
inline constexpr std::size_t page_art_bytes = 768 * 1024;
inline constexpr std::size_t site_icon_bytes = 96 * 1024;

class ArtComApartment {
public:
    ArtComApartment() : result_(CoInitializeEx(nullptr, COINIT_MULTITHREADED)) {}
    ~ArtComApartment() { if (SUCCEEDED(result_)) CoUninitialize(); }
    ArtComApartment(const ArtComApartment&) = delete;
    ArtComApartment& operator=(const ArtComApartment&) = delete;
    bool available() const { return SUCCEEDED(result_) || result_ == RPC_E_CHANGED_MODE; }
private:
    HRESULT result_;
};

// Open only the image container here. The host can decode the accepted JPEG/PNG.
inline album_art_data_ptr site_art(std::span<const unsigned char> bytes, bool brand) {
    if (bytes.empty() || bytes.size() > (brand ? site_icon_bytes : page_art_bytes)) return {};
    ArtComApartment apartment;
    if (!apartment.available()) return {};
    pfc::com_ptr_t<IWICImagingFactory> wic;
    if (FAILED(CoCreateInstance(CLSID_WICImagingFactory, nullptr, CLSCTX_INPROC_SERVER,
                                __uuidof(IWICImagingFactory), wic.receive_void_ptr()))) return {};
    pfc::com_ptr_t<IStream> stream;
    stream.attach(SHCreateMemStream(bytes.data(), static_cast<UINT>(bytes.size())));
    if (stream.is_empty()) return {};
    pfc::com_ptr_t<IWICBitmapDecoder> decoder;
    if (FAILED(wic->CreateDecoderFromStream(stream.get_ptr(), nullptr, WICDecodeMetadataCacheOnDemand,
                                            decoder.receive_ptr()))) return {};
    GUID format{};
    if (FAILED(decoder->GetContainerFormat(&format))) return {};
    if (!brand && format != GUID_ContainerFormatJpeg && format != GUID_ContainerFormatPng) return {};
    if (brand && format != GUID_ContainerFormatIco && format != GUID_ContainerFormatPng &&
        format != GUID_ContainerFormatJpeg && format != GUID_ContainerFormatGif) return {};
    UINT count = 1;
    if (brand && FAILED(decoder->GetFrameCount(&count))) return {};
    pfc::com_ptr_t<IWICBitmapFrameDecode> chosen;
    UINT width = 0, height = 0;
    for (UINT index = 0; index < std::min(count, 32u); ++index) {
        pfc::com_ptr_t<IWICBitmapFrameDecode> frame;
        UINT w = 0, h = 0;
        if (FAILED(decoder->GetFrame(index, frame.receive_ptr())) || FAILED(frame->GetSize(&w, &h))) continue;
        const auto edge = brand ? 256u : 4096u;
        if (!w || !h || w > edge || h > edge || std::min(w, h) * 5 < std::max(w, h) * 4) continue;
        if (w * h <= width * height) continue;
        chosen = frame;
        width = w;
        height = h;
    }
    if (chosen.is_empty()) return {};
    if (!brand || format == GUID_ContainerFormatJpeg || format == GUID_ContainerFormatPng)
        return album_art_data_impl::g_create(bytes.data(), bytes.size());

    // Some frontends accept JPEG/PNG only. Convert an ICO frame to PNG in memory.
    pfc::com_ptr_t<IStream> png;
    pfc::com_ptr_t<IWICBitmapEncoder> encoder;
    pfc::com_ptr_t<IWICBitmapFrameEncode> frame;
    pfc::com_ptr_t<IWICFormatConverter> converter;
    auto pixels = GUID_WICPixelFormat32bppBGRA;
    if (FAILED(CreateStreamOnHGlobal(nullptr, TRUE, png.receive_ptr())) ||
        FAILED(wic->CreateEncoder(GUID_ContainerFormatPng, nullptr, encoder.receive_ptr())) ||
        FAILED(encoder->Initialize(png.get_ptr(), WICBitmapEncoderNoCache)) ||
        FAILED(encoder->CreateNewFrame(frame.receive_ptr(), nullptr)) ||
        FAILED(frame->Initialize(nullptr)) || FAILED(frame->SetSize(width, height)) ||
        FAILED(frame->SetPixelFormat(&pixels)) ||
        FAILED(wic->CreateFormatConverter(converter.receive_ptr())) ||
        FAILED(converter->Initialize(chosen.get_ptr(), pixels, WICBitmapDitherTypeNone, nullptr, 0,
                                      WICBitmapPaletteTypeCustom)) ||
        FAILED(frame->WriteSource(converter.get_ptr(), nullptr)) ||
        FAILED(frame->Commit()) || FAILED(encoder->Commit())) return {};
    LARGE_INTEGER beginning{};
    STATSTG stats{};
    if (FAILED(png->Stat(&stats, STATFLAG_NONAME)) || !stats.cbSize.QuadPart ||
        stats.cbSize.QuadPart > page_art_bytes || FAILED(png->Seek(beginning, STREAM_SEEK_SET, nullptr))) return {};
    std::vector<unsigned char> encoded(static_cast<std::size_t>(stats.cbSize.QuadPart));
    ULONG written = 0;
    if (FAILED(png->Read(encoded.data(), static_cast<ULONG>(encoded.size()), &written)) || written != encoded.size())
        return {};
    return album_art_data_impl::g_create(encoded.data(), encoded.size());
}
}
