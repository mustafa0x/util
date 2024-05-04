#!/usr/bin/env zx

import path from 'path'

$.quote = _ => _ // disable quoting

const video_path = process.argv[3]
const v_path_no_ext = video_path.replace(/\.[^/.]+$/, "")
const prompt = 'summarize as a list of bullet points. the summary must be in english. be concise but thorough. do not change the grammatical point of view'

const {ffprobe} = await $`ffprobe -v quiet -print_format json -show_streams ${video_path}`.quiet()
const aud_stream = JSON.parse(ffprobe).streams.find(stream => stream.codec_type === 'audio')
if (!aud_stream) {
    console.error('Error: No audio stream found in video.')
    process.exit()
}

const {bit_rate, duration, codec_name} = aud_stream
const supported_formats = ['flac', 'm4a', 'mp3', 'mp4', 'mpeg', 'mpga', 'oga', 'ogg', 'wav', 'webm']
const supported = supported_formats.includes(codec_name)
let audio_path = v_path_no_ext + '.' + (supported ? codec_name : 'ogg')
const extract_audio = fs.existsSync(audio_path)
if (extract_audio)
    await $`ffmpeg -i ${video_path} -vn -c:a ${supported ? 'copy' : 'libopus -b:a 64k'} ${audio_path}`.quiet()
else
    console.log('Audio stream already extracted.')

const size_mb = ((+bit_rate * +duration) / 8 / 1024 / 1024).toFixed(2) // assumes cbr
console.log(`Estimated audio stream size: ${size_mb}mb`)

const formData = new FormData()
formData.append('file', new Blob([fs.readFileSync(audio_path)]), path.basename(audio_path))
formData.append('model', 'whisper-1')
formData.append('response_format', 'text')

try {
    const response = await fetch('https://api.openai.com/v1/audio/transcriptions', {
        method: 'POST',
        headers: {
            Authorization: `Bearer ${process.env.OPENAI_API_KEY}`,
        },
        body: formData,
    })
    const data = await response.text()
    if (data.startsWith('{')) {
        console.log(data)
        process.exit()
    }

    const p = $`subl`
    p.stdin.write(data + '\n\n' + prompt)
    p.stdin.end()
    await p
}
catch (error) {
    console.error('Error during transcription process:', error)
}
finally {
    if (extract_audio)
        await fs.promises.unlink(audio_path)
}
