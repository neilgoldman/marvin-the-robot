import speech_recognition
from speech_recognition import AudioSource, AudioData, WaitTimeoutError
import pyttsx
import requests
from datetime import datetime
import re, threading
import os, math, collections, audioop, itertools

"""
A lot of the code and library choices are from
https://ggulati.wordpress.com/2016/02/24/coding-jarvis-in-python-3-in-2016/

"""

wemo_api_url = "http://localhost:5000/api/device/"
bedroom_lamp_str = 'bedroomlamp'
speakers_str = 'speakers'

speech_engine = pyttsx.init('espeak')
speech_engine.setProperty('rate', 150)

class Marvin:
	_heard_name = False
	_time_heard_name = None
	match_words = []
	
	@staticmethod 
	def compile_match_words():
		print('Compiling keywords.')
		lights_re = re.compile(r'.*\b(lights?|lamp)\b.*')
		speakers_re = re.compile(r'.*\b(speakers?|monitors?)\b.*')
		on_re = re.compile(r'.*\b(on)\b.*')
		off_re = re.compile(r'.*\b(off)\b.*')
		toggle_re = re.compile(r'.*\b(get|hit|switch|toggle)\b.*')
		Marvin.match_words = (lights_re, speakers_re, on_re, off_re, toggle_re)
		print('Done')
		
	@staticmethod
	def heard_name():
		Marvin._heard_name = True
		Marvin._time_heard_name = datetime.now()
		print('I heard my name!\n')
		os.system("xset dpms force on")
	
	@staticmethod
	def check_if_still_listening():
		if not Marvin._heard_name:
			return False
		if Marvin._time_heard_name and (datetime.now() - Marvin._time_heard_name).seconds > 20:
			Marvin._heard_name = False
		return Marvin._heard_name


def find_microphone(sample_rate, chunk_size):
	for i, mic_name in enumerate(speech_recognition.Microphone.list_microphone_names()):
		if 'USB audio' in mic_name:
			return speech_recognition.Microphone(device_index=i, sample_rate=sample_rate, chunk_size=chunk_size)
	raise Exception("Could not find microphone")

def speak(text):
	speech_engine.say(text)
	speech_engine.runAndWait()

def listen(mic, recognizer=speech_recognition.Recognizer()):
	stop_callback = custom_listen_in_background(recognizer, mic, recognize_and_respond)
	print('Listening.')
	return stop_callback

def recognize_and_respond(recognizer, audio_data):
	print('Recognizer callback')
	decoder = recognize_sync(recognizer, audio_data, show_all=True)
	possible_keywords = []

	match_words = Marvin.match_words
	heard_marvin = False

	for best in itertools.islice(decoder.nbest(), 0, recognizer.num_n_best):
		words = best.hypstr
		if not words:
			print('no words found')
			break
		# print(words)
		if any(name in words for name in ('arvin', 'artin', 'marlin', 'marten', 'margaret')):
			heard_marvin = True
		for match_re in match_words:
			m = match_re.match(words)
			if m:
				possible_keywords.append(m.group(1))
	
	if heard_marvin:
		Marvin.heard_name()
	elif not Marvin.check_if_still_listening():
		return
	
	possible_keywords = ' '.join(possible_keywords)
	on_count = possible_keywords.count(' on ')
	off_count = possible_keywords.count(' off ')
	toggle_count = sum(map(possible_keywords.count, (' get' , ' hit ', ' switch ', ' toggle ')))
	
	# print(possible_keywords)
	# print(on_count)
	# print(off_count)
	# print(toggle_count)
	
	if (on_count + off_count + toggle_count) < 3:
		return
	
	device_str = ''
	if ('speaker' in possible_keywords) or ('monitor' in possible_keywords):
		device_str = speakers_str
	if ('light' in possible_keywords) or ('lamp' in possible_keywords):
		device_str = bedroom_lamp_str
	
	if device_str:
		if on_count > off_count:
			print('Turning ' + device_str + ' on')
			requests.post(wemo_api_url + device_str, data = {'state': 'on'})
		elif off_count > on_count:
			print('Turning ' + device_str + ' off')
			requests.post(wemo_api_url + device_str, data = {'state': 'off'})
		elif toggle_count > (on_count + off_count):
			print('Toggling ' + device_str)
			requests.post(wemo_api_url + device_str, data = {'state': 'toggle'})


def recognize_sync(recognizer, audio_data, show_all=False):
		try:
			return recognizer.recognize_sphinx(audio_data, show_all=show_all)
		except speech_recognition.UnknownValueError:
			print("Could not understand audio")
			return None
		except speech_recognition.RequestError as e:
			print("Recog Error; {0}".format(e))
			return None
		return None

def custom_listen(self, source, timeout = None):
        """
        Records a single phrase from ``source`` (an ``AudioSource`` instance) into an ``AudioData`` instance, which it returns.
        This is done by waiting until the audio has an energy above ``recognizer_instance.energy_threshold`` (the user has started speaking), and then recording until it encounters ``recognizer_instance.pause_threshold`` seconds of non-speaking or there is no more audio input. The ending silence is not included.
        The ``timeout`` parameter is the maximum number of seconds that it will wait for a phrase to start before giving up and throwing an ``speech_recognition.WaitTimeoutError`` exception. If ``timeout`` is ``None``, it will wait indefinitely.
        """
        assert isinstance(source, AudioSource), "Source must be an audio source"
        assert source.stream is not None, "Audio source must be opened before recording - see documentation for `AudioSource`"
        assert self.pause_threshold >= self.non_speaking_duration >= 0

        seconds_per_buffer = (source.CHUNK + 0.0) / source.SAMPLE_RATE
        pause_buffer_count = int(math.ceil(self.pause_threshold / seconds_per_buffer)) # number of buffers of non-speaking audio before the phrase is complete
        phrase_buffer_count = int(math.ceil(self.phrase_threshold / seconds_per_buffer)) # minimum number of buffers of speaking audio before we consider the speaking audio a phrase
        non_speaking_buffer_count = int(math.ceil(self.non_speaking_duration / seconds_per_buffer)) # maximum number of buffers of non-speaking audio to retain before and after

        # read audio input for phrases until there is a phrase that is long enough
        elapsed_time = 0 # number of seconds of audio read
        while True:
            frames = collections.deque()

            # store audio input until the phrase starts
            while True:
                elapsed_time += seconds_per_buffer
                if timeout and elapsed_time > timeout: # handle timeout if specified
                    raise WaitTimeoutError("listening timed out")

                buffer = source.stream.read(source.CHUNK)
                if len(buffer) == 0: break # reached end of the stream
                frames.append(buffer)
                if len(frames) > non_speaking_buffer_count: # ensure we only keep the needed amount of non-speaking buffers
                    frames.popleft()

                # detect whether speaking has started on audio input
                energy = audioop.rms(buffer, source.SAMPLE_WIDTH) # energy of the audio signal
                if energy > self.energy_threshold: break

                # dynamically adjust the energy threshold using assymmetric weighted average
                if self.dynamic_energy_threshold:
                    damping = self.dynamic_energy_adjustment_damping ** seconds_per_buffer # account for different chunk sizes and rates
                    target_energy = energy * self.dynamic_energy_ratio
                    self.energy_threshold = self.energy_threshold * damping + target_energy * (1 - damping)

            # read audio input until the phrase ends
            pause_count, phrase_count = 0, 0
            while True:
                elapsed_time += seconds_per_buffer

                buffer = source.stream.read(source.CHUNK)
                if len(buffer) == 0: break # reached end of the stream
                frames.append(buffer)
                phrase_count += 1

                # check if speaking has stopped for longer than the pause threshold on the audio input
                energy = audioop.rms(buffer, source.SAMPLE_WIDTH) # energy of the audio signal
                if energy > self.energy_threshold:
                    pause_count = 0
                else:
                    pause_count += 1
                if pause_count > pause_buffer_count: # end of the phrase
                    break

            # check how long the detected phrase is, and retry listening if the phrase is too short
            phrase_count -= pause_count
            if phrase_count >= phrase_buffer_count: break # phrase is long enough, stop listening

        # obtain frame data
        for i in range(pause_count - non_speaking_buffer_count): frames.pop() # remove extra non-speaking frames at the end
        frame_data = b"".join(list(frames))

        return AudioData(frame_data, source.SAMPLE_RATE, source.SAMPLE_WIDTH)

def custom_listen_in_background(recognizer, source, callback):
	assert isinstance(source, AudioSource), "Source must be an audio source"
	running = [True]
	def threaded_listen():
		with source as s:
			while running[0]:
				try: # listen for 1 second, then check again if the stop function has been called
					print('Listening')
					audio = custom_listen(recognizer, s, timeout=1.0)
				except WaitTimeoutError: # listening timed out, just try again
					print('Adjusting for ambient noise')
					recognizer.adjust_for_ambient_noise(s, duration=0.5)
					recognizer.energy_threshold = recognizer.energy_threshold - (recognizer.energy_threshold * 0.2)
					if not Marvin.check_if_still_listening():
						os.system("xset dpms force off")
					print(recognizer.energy_threshold)
				else:
					if running[0]: callback(recognizer, audio)
	def stopper():
		running[0] = False
		listener_thread.join() # block until the background thread is done, which can be up to 1 second
	listener_thread = threading.Thread(target=threaded_listen)
	listener_thread.daemon = True
	listener_thread.start()
	return stopper
	

def main():
	Marvin.compile_match_words()
	print('Opening Microphone')
	mic = find_microphone(sample_rate=16000, chunk_size=2048)
	print('Loading Recognizer')
	recognizer = speech_recognition.Recognizer()
	recognizer.energy_threshold = 1000
	recognizer.dynamic_energy_threshold = True
	recognizer.dynamic_energy_adjustment_damping = 0.2
	recognizer.dynamic_energy_adjustment_ratio = 1.3
	recognizer.pause_threshold = 0.2
	recognizer.non_speaking_duration = 0.1
	recognizer.num_n_best = 20

	print('Streaming from Microphone.')
	stop_listening= listen(mic, recognizer=recognizer)
	from time import sleep
	try:
		while True:
			sleep(1)
	except KeyboardInterrupt:
		print('Handling quit command')
		stop_listening()
		exit()


if __name__ == '__main__':
	main()
