/* Моя программа SmileKit — Telegram Mini App.
   Весь UI собирается здесь: сервер отдаёт только состояние курса (см.
   miniapp.py), контент академии лежит в content.js. */

import { ARTICLES, CATEGORIES, FACTS, ILLUSTRATIONS, QUIZ } from '/app/content.js?v=20260820'

const tg = window.Telegram?.WebApp
const API = '/app/api'

let state = null
let activeTab = 'today'
let learnCategory = 'all'
let chatMessages = []
let chatBusy = false

const $ = (selector, root = document) => root.querySelector(selector)
const el = (id) => document.getElementById(id)

/* ---------------------------------------------------------------- утилиты */

function esc(value) {
  return String(value).replace(/[&<>"']/g, (c) => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ))
}

function haptic(style = 'light') {
  try { tg?.HapticFeedback?.impactOccurred(style) } catch (e) {}
}

function notify(type = 'success') {
  try { tg?.HapticFeedback?.notificationOccurred(type) } catch (e) {}
}

let toastTimer = null
function toast(text) {
  const node = el('toast')
  node.textContent = text
  node.classList.add('is-visible')
  clearTimeout(toastTimer)
  toastTimer = setTimeout(() => node.classList.remove('is-visible'), 2400)
}

function plural(n, one, few, many) {
  const mod10 = n % 10
  const mod100 = n % 100
  if (mod10 === 1 && mod100 !== 11) return one
  if (mod10 >= 2 && mod10 <= 4 && (mod100 < 10 || mod100 >= 20)) return few
  return many
}

/* Параметры запуска Telegram кладёт в хэш адреса, а SDK лишь читает их оттуда
   в tg.initData. Если telegram-web-app.js не загрузился или не успел
   инициализироваться до первого запроса, tg.initData пуст — тогда разбираем
   хэш сами. Значение фиксируем при старте: хэш может быть затёрт навигацией,
   а подпись нужна всю сессию. */
const LAUNCH_DATA = (() => {
  if (tg?.initData) return tg.initData
  const hash = location.hash.replace(/^#/, '')
  return new URLSearchParams(hash).get('tgWebAppData') || ''
})()

async function api(path, options = {}) {
  const response = await fetch(API + path, {
    ...options,
    headers: {
      'X-Telegram-Init-Data': tg?.initData || LAUNCH_DATA,
      ...(options.body instanceof FormData ? {} : { 'Content-Type': 'application/json' }),
      ...(options.headers || {}),
    },
  })
  if (!response.ok) {
    const detail = await response.json().catch(() => ({}))
    throw Object.assign(new Error(detail.error || 'request_failed'), {
      status: response.status,
      reason: detail.reason,
    })
  }
  return response.json()
}

async function push(path, body, method = 'POST') {
  const next = await api(path, {
    method,
    body: body instanceof FormData ? body : JSON.stringify(body || {}),
  })
  if (next && next.course) {
    state = next
    render()
  }
  return next
}

/* --------------------------------------------------------------- шторка */

function openSheet(html) {
  const sheet = el('sheet')
  el('sheetBody').innerHTML = html
  sheet.hidden = false
  sheet.classList.remove('is-closing')
  document.body.style.overflow = 'hidden'
  syncBackButton()
  return el('sheetBody')
}

function closeSheet() {
  const sheet = el('sheet')
  if (sheet.hidden) return
  sheet.classList.add('is-closing')
  document.body.style.overflow = ''
  setTimeout(() => { sheet.hidden = true; sheet.classList.remove('is-closing') }, 290)
  syncBackButton()
}

/* Системная кнопка «назад» Telegram вместо экранной: на телефоне это тот же
   жест, которым закрывают экран в любом другом приложении. Прячем её на
   корневой вкладке — тогда следующий «назад» сворачивает само приложение,
   как и ожидается. */
function syncBackButton() {
  const back = tg?.BackButton
  if (!back) return
  const sheetOpen = !el('sheet').hidden
  if (sheetOpen || activeTab !== 'today') back.show()
  else back.hide()
}

function handleBack() {
  if (!el('sheet').hidden) closeSheet()
  else if (activeTab !== 'today') go('today')
}

document.addEventListener('click', (event) => {
  if (event.target.closest('[data-close-sheet]')) closeSheet()
})

/* ------------------------------------------------------------- конфетти */

function confetti() {
  const canvas = el('confetti')
  const ctx = canvas.getContext('2d')
  const dpr = window.devicePixelRatio || 1
  canvas.hidden = false
  canvas.width = innerWidth * dpr
  canvas.height = innerHeight * dpr
  ctx.scale(dpr, dpr)
  const colors = ['#5115A4', '#8A68AD', '#C9B6F0', '#17B79A', '#E0A32E']
  const pieces = Array.from({ length: 90 }, () => ({
    x: Math.random() * innerWidth,
    y: -20 - Math.random() * innerHeight * 0.5,
    r: 4 + Math.random() * 6,
    vy: 2 + Math.random() * 3.4,
    vx: -1.2 + Math.random() * 2.4,
    rot: Math.random() * Math.PI,
    vr: -0.12 + Math.random() * 0.24,
    color: colors[Math.floor(Math.random() * colors.length)],
  }))
  const started = performance.now()
  function frame(now) {
    ctx.clearRect(0, 0, innerWidth, innerHeight)
    pieces.forEach((p) => {
      p.x += p.vx; p.y += p.vy; p.rot += p.vr
      ctx.save()
      ctx.translate(p.x, p.y)
      ctx.rotate(p.rot)
      ctx.fillStyle = p.color
      ctx.globalAlpha = Math.max(0, 1 - (now - started) / 3600)
      ctx.fillRect(-p.r / 2, -p.r / 2, p.r, p.r * 1.5)
      ctx.restore()
    })
    if (now - started < 3600) requestAnimationFrame(frame)
    else { ctx.clearRect(0, 0, innerWidth, innerHeight); canvas.hidden = true }
  }
  requestAnimationFrame(frame)
}

/* ------------------------------------------------------------ онбординг */

/* Онбординг идёт диалогом, а не формой: приложение задаёт три вопроса, клиент
   отвечает своими словами, а модель разбирает ответ и реагирует на него живой
   фразой. Варианты кнопками остаются рядом — они же страховка, если модель
   недоступна. */

const ONBOARDING_STEPS = [
  {
    key: 'goal',
    question: 'Какого результата ты хочешь от курса?',
    chips: [
      { value: 'refresh', label: 'Немного освежить' },
      { value: 'lighter', label: 'Заметно светлее' },
      { value: 'max', label: 'Максимум' },
    ],
  },
  {
    key: 'drink',
    question: 'А что ты чаще пьёшь в течение дня?',
    chips: [
      { value: 'coffee', label: 'Кофе' },
      { value: 'tea', label: 'Чай' },
      { value: 'soda', label: 'Газировку' },
      { value: 'none', label: 'Почти только воду' },
    ],
  },
  {
    key: 'sensitivity',
    question: 'И последнее: есть ли у тебя чувствительность зубов?',
    chips: [
      { value: 'yes', label: 'Да, есть' },
      { value: 'sometimes', label: 'Иногда' },
      { value: 'no', label: 'Нет' },
    ],
  },
]

// Фразы подбираются под шаг: на разборе ответа честно «слушаем», на сборке
// программы — «считаем». Общий счётчик крутит их по кругу, пока идёт запрос.
const THINKING_PHRASES = [
  'Вчитываемся в ответ',
  'Сверяемся с базой знаний',
  'Прикидываем, что это значит для курса',
  'Подбираем формулировку',
]
const BUILDING_PHRASES = [
  'Раскладываем 14 дней по полочкам',
  'Считаем, на какие дни ставить процедуры',
  'Отбираем задачи под твои ответы',
  'Прикидываем, где эмаль рискует потемнеть',
  'Расставляем дни для фото',
  'Собираем персональные подсказки',
  'Проверяем, что всё сходится',
]

let onboardingStep = 0
let onboardingBusy = false
const onboardingAnswers = {}
const onboardingLog = []

const TABS = ['today', 'progress', 'learn', 'ask', 'profile']

/* Онбординг и финальный экран занимают весь #app целиком, поэтому пять
   секций-вкладок приходится собирать обратно перед первым go(). */
function restoreShell() {
  const app = el('app')
  app.classList.remove('app--plain', 'app--chat')
  app.innerHTML = TABS.map((tab) => (
    `<section class="screen" id="screen-${tab}" data-tab="${tab}"${tab === activeTab ? '' : ' hidden'}></section>`
  )).join('')
}

function renderOnboarding() {
  el('tabbar').hidden = true
  const app = el('app')
  app.classList.add('app--chat')
  const name = state?.user?.first_name ? `, ${esc(state.user.first_name)}` : ''
  app.innerHTML = `
    <section class="screen onb-chat">
      <header class="onb-chat__head">
        <span class="onb__logo">smilekit</span>
        <span class="onb-chat__steps">
          ${ONBOARDING_STEPS.map((_, index) => `<i class="${index < onboardingStep ? 'is-done' : index === onboardingStep ? 'is-active' : ''}"></i>`).join('')}
        </span>
      </header>
      <div class="chat" id="onbChat"></div>
      <div class="composer" id="onbComposer">
        <textarea id="onbInput" rows="1" placeholder="Ответь своими словами…" maxlength="500"></textarea>
        <button class="composer__send" id="onbSend" aria-label="Отправить">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12h15M13 5.5 19.5 12 13 18.5"/></svg>
        </button>
      </div>
    </section>`

  if (!onboardingLog.length) {
    onboardingLog.push(
      { role: 'bot', text: `Привет${name} 👋 Я помогу собрать твою программу на 14 дней.` },
      { role: 'bot', text: 'Три коротких вопроса — и всё будет готово. Отвечай как удобно, своими словами.' },
      { role: 'bot', text: ONBOARDING_STEPS[0].question, chips: 0 },
    )
  }
  paintOnboarding()

  const input = $('#onbInput', app)
  input.addEventListener('input', () => {
    input.style.height = 'auto'
    input.style.height = `${Math.min(120, input.scrollHeight)}px`
  })
  input.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault()
      submitOnboarding(input.value)
    }
  })
  $('#onbSend', app).addEventListener('click', () => submitOnboarding(input.value))
}

function paintOnboarding() {
  const chat = el('onbChat')
  if (!chat) return
  chat.innerHTML = onboardingLog.map((item) => {
    if (item.role === 'thinking') {
      return `<div class="bubble bubble--bot thinking-bubble">
        <span class="typing"><i></i><i></i><i></i></span>
        <span class="thinking-phrase" id="thinkingPhrase">${esc(item.phrase)}</span>
      </div>`
    }
    const bubble = `<div class="bubble bubble--${item.role === 'me' ? 'me' : 'bot'}">${esc(item.text)}</div>`
    if (item.chips === undefined) return bubble
    const step = ONBOARDING_STEPS[item.chips]
    return bubble + `
      <div class="suggests" data-chips="${item.chips}">
        ${step.chips.map((chip) => `<button class="suggest" data-chip="${chip.value}" data-label="${esc(chip.label)}">${esc(chip.label)}</button>`).join('')}
      </div>`
  }).join('')

  chat.querySelectorAll('[data-chip]').forEach((button) => {
    button.addEventListener('click', () => {
      if (onboardingBusy) return
      // По кнопке значение уже известно — модель для разбора не нужна.
      submitOnboarding(button.dataset.label, button.dataset.chip)
    })
  })
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }))
}

/* Крутит фразы, пока идёт запрос. Возвращает функцию остановки. */
function startThinking(phrases) {
  let index = 0
  onboardingLog.push({ role: 'thinking', phrase: phrases[0] })
  paintOnboarding()
  const timer = setInterval(() => {
    index = (index + 1) % phrases.length
    const node = el('thinkingPhrase')
    if (!node) return
    node.style.animation = 'none'
    void node.offsetWidth
    node.textContent = phrases[index]
    node.style.animation = 'phrase-in .4s var(--ease) both'
  }, 1600)
  return () => {
    clearInterval(timer)
    const at = onboardingLog.findIndex((item) => item.role === 'thinking')
    if (at !== -1) onboardingLog.splice(at, 1)
  }
}

async function submitOnboarding(text, knownValue = null) {
  const answer = String(text || '').trim()
  if (!answer || onboardingBusy) return
  const step = ONBOARDING_STEPS[onboardingStep]
  onboardingBusy = true
  haptic()

  const input = el('onbInput')
  if (input) { input.value = ''; input.style.height = 'auto' }
  // Кнопки отвеченного вопроса убираем — иначе на них можно нажать второй раз.
  onboardingLog.forEach((item) => { if (item.chips !== undefined) delete item.chips })
  onboardingLog.push({ role: 'me', text: answer })

  let value = knownValue
  let ack = ''
  if (!value) {
    const stop = startThinking(THINKING_PHRASES)
    try {
      const result = await api('/onboarding/reply', {
        method: 'POST',
        body: JSON.stringify({ step: step.key, text: answer }),
      })
      value = result.value
      ack = result.ack
    } catch (e) {
      value = null
    }
    stop()
  }

  if (!value) {
    onboardingLog.push({
      role: 'bot',
      text: ack || 'Не совсем понял ответ. Выбери, пожалуйста, ближайший вариант 👇',
      chips: onboardingStep,
    })
    onboardingBusy = false
    paintOnboarding()
    return
  }

  onboardingAnswers[step.key] = value
  if (ack) onboardingLog.push({ role: 'bot', text: ack })

  if (onboardingStep < ONBOARDING_STEPS.length - 1) {
    onboardingStep += 1
    onboardingLog.push({ role: 'bot', text: ONBOARDING_STEPS[onboardingStep].question, chips: onboardingStep })
    onboardingBusy = false
    paintOnboarding()
    renderOnboardingSteps()
    return
  }

  el('onbComposer').hidden = true
  const stop = startThinking(BUILDING_PHRASES)
  try {
    state = await api('/onboarding', { method: 'POST', body: JSON.stringify(onboardingAnswers) })
    stop()
    notify('success')
    renderWelcome()
  } catch (e) {
    stop()
    onboardingBusy = false
    el('onbComposer').hidden = false
    onboardingLog.push({ role: 'bot', text: 'Не получилось сохранить ответы. Попробуй отправить ещё раз.' })
    paintOnboarding()
  }
}

function renderOnboardingSteps() {
  const dots = document.querySelector('.onb-chat__steps')
  if (!dots) return
  dots.innerHTML = ONBOARDING_STEPS.map((_, index) => (
    `<i class="${index < onboardingStep ? 'is-done' : index === onboardingStep ? 'is-active' : ''}"></i>`
  )).join('')
}

function renderWelcome() {
  const app = el('app')
  app.classList.remove('app--chat')
  app.classList.add('app--plain')
  const goalText = {
    refresh: 'освежить улыбку',
    lighter: 'сделать зубы заметно светлее',
    max: 'получить максимальный результат',
  }[state.user.goal]
  app.innerHTML = `
    <section class="screen finale">
      <div class="finale__emoji">💜</div>
      <h1 class="finale__title">Программа готова</h1>
      <p class="finale__lead">Мы подготовили рекомендации специально для тебя: цель — ${goalText}.</p>
      ${state.plan ? `
        <div class="hint" style="margin-top:20px;text-align:left">
          <span class="hint__emoji">✨</span>
          <span>
            <span class="hint__title">Что важно именно тебе</span>
            <span class="hint__text">${esc(state.plan)}</span>
          </span>
        </div>` : ''}
      <div class="card stack" style="margin-top:14px;text-align:left">
        <p class="muted">Как всё устроено:</p>
        <div class="divider"></div>
        <p class="muted">🦷 <b>7 процедур по 30 минут</b> — через день, ровно на коробку из 14 полосок.</p>
        <p class="muted">🪥 <b>Дни ухода между ними</b> — короткие шаги, чтобы результат закреплялся.</p>
        <p class="muted">✨ <b>Фото на 1-й, 7-й и 14-й день</b> — сравнение до и после.</p>
        <p class="muted">🎁 <b>Пройдёшь курс — откроется скидка 30%</b> на следующий набор.</p>
      </div>
      <button class="btn" id="startCourse" style="margin-top:20px">Начать первый день</button>
    </section>`
  confetti()
  el('startCourse').addEventListener('click', () => {
    haptic('medium')
    resetFactOfTheDay()
    restoreShell()
    el('tabbar').hidden = false
    go('today')
  })
}


function personalTip() {
  const { drink, sensitivity, goal } = state.user
  const day = state.course.day
  if (sensitivity === 'yes' && state.today.is_procedure_day) {
    return {
      emoji: '🫧',
      title: 'Ты говорил(а) о чувствительности',
      text: 'Если сегодня появится заметный дискомфорт — сними полоски раньше и продолжи курс завтра. Пропущенный день не сбивает программу.',
    }
  }
  if (drink === 'coffee' && state.today.is_procedure_day) {
    return {
      emoji: '☕',
      title: 'Ты часто пьёшь кофе',
      text: 'После процедуры постарайся выдержать два часа без кофе — именно в это время эмаль впитывает цвет охотнее всего.',
    }
  }
  if (drink === 'tea' && state.today.is_procedure_day) {
    return {
      emoji: '🍵',
      title: 'Чай красит не слабее кофе',
      text: 'Два часа после процедуры — только вода. Дальше можно вернуться к привычной чашке.',
    }
  }
  if (drink === 'soda') {
    return {
      emoji: '🥤',
      title: 'Про газировку',
      text: 'Пей через трубочку и прополощи рот водой — так красители меньше задерживаются на эмали.',
    }
  }
  if (goal === 'max') {
    return {
      emoji: '🎯',
      title: 'Ты идёшь на максимум',
      text: 'В дни без процедуры держись «белой еды»: вода, молочное, курица, рис. Именно эти дни решают, каким будет финал.',
    }
  }
  if (day >= 10) {
    return {
      emoji: '🔥',
      title: 'Финишная прямая',
      text: 'Осталось совсем немного. Доведи курс до конца — и результат закрепится на месяцы, а не на неделю.',
    }
  }
  return {
    emoji: '💧',
    title: 'Маленькая привычка',
    text: 'Стакан воды после кофе или чая — самый простой способ продлить результат. Работает каждый день курса.',
  }
}

/* Факт дня. Стартовый выбирается по номеру дня, а не случайно: открыв
   приложение дважды за день, клиент увидит тот же факт, а не мельтешение. */
let factIndex = 0

function resetFactOfTheDay() {
  const day = Math.floor(Date.now() / 86400000)
  factIndex = day % FACTS.length
}

function trackHtml() {
  const { day, total_days: total, procedure_days: procedures } = state.course
  return Array.from({ length: total }, (_, index) => {
    const number = index + 1
    const classes = ['track__seg']
    if (procedures.includes(number)) classes.push('track__seg--procedure')
    if (state.check_ins[String(number)]) classes.push('is-done')
    else if (number === day) classes.push('is-today')
    return `<span class="${classes.join(' ')}"></span>`
  }).join('')
}

function renderToday() {
  const screen = el('screen-today')
  const { day, total_days: total } = state.course
  const done = state.today.done
  const percent = Math.round((state.stats.days_done / total) * 100)
  const tip = personalTip()
  const rewardLeft = state.reward.left

  screen.innerHTML = `
    <header class="head">
      <h1 class="head__hello">Привет, ${esc(state.user.first_name || 'друг')} 👋</h1>
      <p class="head__sub">Ты на ${day}-м дне SmileKit${state.streak > 1 ? ` · серия ${state.streak} ${plural(state.streak, 'день', 'дня', 'дней')} 🔥` : ''}</p>
    </header>

    <div class="stack stagger">
      <div class="card glass--sheen">
        <div class="progress__top">
          <span class="progress__day">День ${day} из ${total}</span>
          <span class="progress__pct">${percent}%</span>
        </div>
        <div class="track">${trackHtml()}</div>
        <p class="progress__note">${state.today.is_procedure_day
          ? `Сегодня процедура — ${state.course.procedure_minutes} минут с полосками.`
          : 'Сегодня день ухода: полоски не нужны, эмаль закрепляет результат.'}</p>
      </div>

      <div class="card">
        <span class="eyebrow">Сегодня</span>
        <ul class="tasks" style="margin-top:10px">
          ${state.today.tasks.map((task) => `
            <li>
              <button class="task ${done || state.today.done_tasks.includes(task.id) ? 'is-done' : ''}" data-task="${task.id}">
                <span class="task__box">
                  <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="3.2" stroke-linecap="round" stroke-linejoin="round"><path d="m5 12.5 4.5 4.5L19 7"/></svg>
                </span>
                <span>
                  <span class="task__text"><span class="task__emoji">${task.icon}</span> ${esc(task.text)}</span>
                  <span class="task__hint">${esc(task.hint)}</span>
                </span>
              </button>
            </li>`).join('')}
        </ul>
        ${state.today.is_procedure_day ? `
          <button class="btn btn--ghost" id="openTimer" style="margin-top:12px">
            ⏱ Включить таймер на ${state.course.procedure_minutes} минут
          </button>` : ''}
        <button class="btn ${done ? 'btn--done' : ''}" id="finishDay" style="margin-top:10px">
          ${done ? 'День отмечен ✓' : 'Готово ✓'}
        </button>
        ${done ? '<p class="muted center" style="margin-top:10px">Отличная работа. Возвращайся завтра 💜</p>' : ''}
      </div>

      <div class="hint">
        <span class="hint__emoji">${tip.emoji}</span>
        <span>
          <span class="hint__title">${tip.title}</span>
          <span class="hint__text">${tip.text}</span>
        </span>
      </div>

      <button class="card fact" id="factCard">
        <span class="fact__head">
          <span class="eyebrow">Интересный факт</span>
          <span class="fact__next">Ещё →</span>
        </span>
        <span class="fact__body">
          <span class="fact__emoji">${FACTS[factIndex].emoji}</span>
          <span class="fact__text">${esc(FACTS[factIndex].text)}</span>
        </span>
      </button>

      ${state.course.photo_days.includes(day) && !state.photos.some((p) => p.day === day) ? `
        <button class="card" id="todayPhoto" style="text-align:left;display:flex;align-items:center;gap:13px">
          <span class="article-card__emoji">📸</span>
          <span>
            <span class="article-card__title">${day === 1 ? 'Сделай фото «до»' : day === 14 ? 'Сделай фото «после»' : 'Сделай контрольное фото'}</span>
            <span class="article-card__excerpt">Без него сравнение в конце курса не получится</span>
          </span>
        </button>` : ''}

      ${rewardLeft > 0 && rewardLeft <= 3 ? `
        <div class="card" style="display:flex;align-items:center;gap:13px">
          <span class="article-card__emoji">🎁</span>
          <span>
            <span class="article-card__title">До награды осталось ${rewardLeft} ${plural(rewardLeft, 'процедура', 'процедуры', 'процедур')}</span>
            <span class="article-card__excerpt">Заверши курс и получи −30% на следующий набор</span>
          </span>
        </div>` : ''}

      ${state.reward.unlocked && !state.reward.code ? `
        <button class="reward" id="claimReward" style="width:100%;text-align:left">
          <span class="eyebrow" style="color:rgba(255,255,255,.8)">Курс пройден</span>
          <div class="reward__pct">−30%</div>
          <div class="reward__label">Награда за то, что ты прошёл(ла) программу до конца</div>
          <div class="reward__code">Забрать скидку →</div>
        </button>` : ''}
    </div>`

  screen.querySelectorAll('[data-task]').forEach((button) => {
    button.addEventListener('click', () => {
      haptic()
      button.classList.toggle('is-done')
    })
  })
  $('#factCard', screen).addEventListener('click', () => {
    haptic()
    factIndex = (factIndex + 1) % FACTS.length
    const body = $('.fact__body', screen)
    body.style.animation = 'none'
    void body.offsetWidth
    body.innerHTML = `
      <span class="fact__emoji">${FACTS[factIndex].emoji}</span>
      <span class="fact__text">${esc(FACTS[factIndex].text)}</span>`
    body.style.animation = 'fact-in .38s var(--ease) both'
  })
  $('#openTimer', screen)?.addEventListener('click', openTimer)
  $('#todayPhoto', screen)?.addEventListener('click', () => pickPhoto(day))
  $('#claimReward', screen)?.addEventListener('click', claimReward)
  $('#finishDay', screen).addEventListener('click', async () => {
    const marked = [...screen.querySelectorAll('.task.is-done')].map((n) => n.dataset.task)
    if (state.today.done) {
      await push('/checkin', { day, undo: true })
      toast('Отметка снята')
      return
    }
    notify('success')
    await push('/checkin', { day, tasks: marked })
    if (state.reward.unlocked && !state.reward.code) confetti()
    else if (day >= state.course.total_days) confetti()
    toast(state.streak > 1 ? `Серия ${state.streak} ${plural(state.streak, 'день', 'дня', 'дней')} 🔥` : 'День отмечен ✓')
  })
}

/* --------------------------------------------------------------- таймер */

const TIMER_KEY = 'smilekit_timer_ends_at'
let timerInterval = null

function openTimer() {
  haptic('medium')
  const total = state.course.procedure_minutes * 60
  const body = openSheet(`
    <div class="timer">
      <span class="eyebrow">Процедура</span>
      <div class="timer__clock" id="timerClock">30:00</div>
      <div class="timer__bar"><span class="timer__fill" id="timerFill" style="width:0%"></span></div>
      <p class="timer__note" id="timerNote">Держи рот приоткрытым — из-за влаги результат хуже.</p>
      <div class="timer__actions">
        <button class="btn" id="timerStart">Запустить</button>
        <button class="btn btn--ghost" id="timerStop">Сбросить</button>
      </div>
    </div>`)

  const clock = $('#timerClock', body)
  const fill = $('#timerFill', body)
  const note = $('#timerNote', body)

  function paint() {
    const endsAt = Number(localStorage.getItem(TIMER_KEY) || 0)
    if (!endsAt) {
      clock.textContent = `${state.course.procedure_minutes}:00`
      fill.style.width = '0%'
      return
    }
    const left = Math.max(0, Math.round((endsAt - Date.now()) / 1000))
    clock.textContent = `${String(Math.floor(left / 60)).padStart(2, '0')}:${String(left % 60).padStart(2, '0')}`
    fill.style.width = `${((total - left) / total) * 100}%`
    if (left === 0) {
      localStorage.removeItem(TIMER_KEY)
      clearInterval(timerInterval)
      notify('success')
      note.textContent = 'Готово ✨ Сними полоски, убери гель салфеткой и прополощи рот водой.'
      confetti()
    }
  }

  clearInterval(timerInterval)
  timerInterval = setInterval(paint, 500)
  paint()

  $('#timerStart', body).addEventListener('click', () => {
    haptic('medium')
    localStorage.setItem(TIMER_KEY, String(Date.now() + total * 1000))
    note.textContent = 'Идёт процедура. Приложение можно закрыть — таймер не собьётся.'
    paint()
  })
  $('#timerStop', body).addEventListener('click', () => {
    haptic()
    localStorage.removeItem(TIMER_KEY)
    note.textContent = 'Держи рот приоткрытым — из-за влаги результат хуже.'
    paint()
  })
}

/* ------------------------------------------------------ экран «Прогресс» */

function renderProgress() {
  const screen = el('screen-progress')
  const { total_days: total, procedure_days: procedures, photo_days: photoDays, day } = state.course
  const circumference = 2 * Math.PI * 40

  screen.innerHTML = `
    <header class="head">
      <h1 class="head__title">Прогресс</h1>
      <p class="head__sub">${state.stats.procedures_done} из ${state.stats.procedures_total} процедур выполнено</p>
    </header>

    <div class="stack stagger">
      <div class="card">
        <div class="score">
          <div class="score__ring">
            <svg viewBox="0 0 100 100">
              <defs>
                <linearGradient id="scoreGradient" x1="0" y1="0" x2="1" y2="1">
                  <stop offset="0%" stop-color="#8A68AD"/>
                  <stop offset="100%" stop-color="#5115A4"/>
                </linearGradient>
              </defs>
              <circle class="score__track" cx="50" cy="50" r="40"/>
              <circle class="score__value" cx="50" cy="50" r="40"
                stroke-dasharray="${circumference}" stroke-dashoffset="${circumference}" id="scoreArc"/>
            </svg>
            <span class="score__num">${state.score}</span>
          </div>
          <div class="score__body">
            <h3>Smile Score</h3>
            <p>Игровой показатель того, насколько точно ты идёшь по программе. Растёт за процедуры, уход, фото и обучение.</p>
          </div>
        </div>
      </div>

      <div class="card">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;margin-bottom:14px">
          <span class="eyebrow">Курс</span>
          ${state.streak > 1 ? `<span class="streak">🔥 ${state.streak} ${plural(state.streak, 'день', 'дня', 'дней')} подряд</span>` : ''}
        </div>
        <div class="calendar">
          ${Array.from({ length: total }, (_, index) => {
            const number = index + 1
            const classes = ['cal-day']
            if (procedures.includes(number)) classes.push('is-procedure')
            if (state.check_ins[String(number)]) classes.push('is-done')
            if (number === day) classes.push('is-today')
            if (number > day) classes.push('is-future')
            return `<button class="${classes.join(' ')}" data-day="${number}">
              ${number}<span class="cal-day__mark">${procedures.includes(number) ? '🦷' : '·'}</span>
            </button>`
          }).join('')}
        </div>
        <div class="legend">
          <span><i class="done"></i> Отмечено</span>
          <span><i class="proc"></i> День процедуры</span>
          <span><i class="rest"></i> День ухода</span>
        </div>
      </div>

      <div class="card">
        <span class="eyebrow">Фото прогресса</span>
        <div class="photos" style="margin-top:12px">
          ${photoDays.map((photoDay) => {
            const photo = state.photos.find((p) => p.day === photoDay)
            const label = { 1: 'День 1', 7: 'День 7', 14: 'День 14' }[photoDay]
            const caption = { 1: 'Фото «до»', 7: 'Контрольное', 14: 'Фото «после»' }[photoDay]
            if (photo) {
              return `<button class="photo photo--filled" data-photo-day="${photoDay}">
                <img src="${photo.url}" alt="${caption}" loading="lazy">
                <span class="photo__label">${label}</span>
              </button>`
            }
            const locked = photoDay > day
            return `<button class="photo ${locked ? 'photo__locked' : ''}" data-photo-day="${photoDay}" ${locked ? 'disabled' : ''}>
              <span class="photo__plus">${locked ? '🔒' : '+'}</span>
              <span class="photo__cap">${caption}</span>
              <span class="photo__label">${label}</span>
            </button>`
          }).join('')}
        </div>
        <p class="muted" style="margin-top:12px">Снимай при одном и том же свете и с одного ракурса — тогда сравнение будет честным.</p>
      </div>

      ${renderCompare()}
    </div>`

  requestAnimationFrame(() => {
    const arc = $('#scoreArc', screen)
    if (arc) arc.style.strokeDashoffset = String(circumference * (1 - state.score / 100))
  })

  screen.querySelectorAll('[data-day]').forEach((button) => {
    button.addEventListener('click', () => openDay(Number(button.dataset.day)))
  })
  screen.querySelectorAll('[data-photo-day]').forEach((button) => {
    button.addEventListener('click', () => {
      const photoDay = Number(button.dataset.photoDay)
      const photo = state.photos.find((p) => p.day === photoDay)
      if (photo) openPhoto(photo)
      else pickPhoto(photoDay)
    })
  })
  setupCompare(screen)
}

function renderCompare() {
  const first = state.photos.find((p) => p.day === 1)
  const last = [...state.photos].reverse().find((p) => p.day !== 1)
  if (!first || !last) return ''
  return `
    <div class="card">
      <span class="eyebrow">Как меняется улыбка</span>
      <div class="compare" id="compare" style="--split:50%;margin-top:12px">
        <img src="${first.url}" alt="До">
        <img class="compare__after" src="${last.url}" alt="После">
        <span class="compare__tag compare__tag--left">День 1</span>
        <span class="compare__tag compare__tag--right">День ${last.day}</span>
        <span class="compare__handle">
          <span class="compare__grip">
            <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6-5 6 5 6M15 6l5 6-5 6"/></svg>
          </span>
        </span>
      </div>
      <p class="muted center" style="margin-top:12px">Потяни шторку в стороны</p>
    </div>`
}

function setupCompare(root) {
  const node = $('#compare', root)
  if (!node) return
  let dragging = false
  const move = (event) => {
    const rect = node.getBoundingClientRect()
    const x = event.clientX - rect.left
    node.style.setProperty('--split', `${Math.max(0, Math.min(100, (x / rect.width) * 100))}%`)
  }
  // Флаг, а не event.buttons: у тач-указателя оба поля остаются нулевыми,
  // и шторка не двигалась бы вовсе — а это основной способ ввода здесь.
  node.addEventListener('pointerdown', (event) => {
    dragging = true
    node.setPointerCapture(event.pointerId)
    move(event)
  })
  node.addEventListener('pointermove', (event) => { if (dragging) move(event) })
  node.addEventListener('pointerup', () => { dragging = false })
  node.addEventListener('pointercancel', () => { dragging = false })
}

// id задач приходят с сервера (day_plan в miniapp.py) — здесь только подписи
// для истории дня, чтобы в карточке не светились технические идентификаторы.
const TASK_LABELS = {
  procedure: 'Процедура с полосками',
  no_staining: 'Без красящих напитков 2 часа',
  care: 'Мягкая чистка зубов',
  water: 'Полоскание водой после кофе или чая',
  photo: 'Фото прогресса',
  sensitivity: 'Проверка ощущений',
}

function openDay(day) {
  haptic()
  const entry = state.check_ins[String(day)]
  const isProcedure = state.course.procedure_days.includes(day)
  const photo = state.photos.find((p) => p.day === day)
  openSheet(`
    <span class="eyebrow">День ${day} из ${state.course.total_days}</span>
    <h2 style="margin-top:10px;font-size:22px;color:var(--purple-deep)">
      ${isProcedure ? `Процедура · ${state.course.procedure_minutes} минут` : 'День ухода'}
    </h2>
    <p class="muted" style="margin-top:10px">
      ${entry
        ? 'День отмечен. Так и держать 💜'
        : day > state.course.day
          ? 'Этот день ещё впереди.'
          : 'День не отмечен. Пропуск не ломает курс — просто продолжай дальше.'}
    </p>
    ${photo ? `<img src="${photo.url}" alt="Фото дня ${day}" style="margin-top:16px;border-radius:18px">` : ''}
    ${entry?.tasks?.length ? `
      <div class="divider" style="margin:18px 0"></div>
      <span class="eyebrow">Что было отмечено</span>
      <ul class="reader__blocks" style="margin-top:10px">
        ${entry.tasks.map((id) => `<li class="muted">✓ ${esc(TASK_LABELS[id] || id)}</li>`).join('')}
      </ul>` : ''}`)
}

function openPhoto(photo) {
  haptic()
  const body = openSheet(`
    <span class="eyebrow">День ${photo.day}</span>
    <img src="${photo.url}" alt="Фото дня ${photo.day}" style="margin-top:14px;border-radius:20px">
    <button class="btn btn--ghost" id="replacePhoto" style="margin-top:16px">Переснять</button>
    <button class="btn btn--ghost" id="deletePhoto" style="margin-top:10px;color:#C0392B">Удалить фото</button>`)
  $('#replacePhoto', body).addEventListener('click', () => { closeSheet(); pickPhoto(photo.day) })
  $('#deletePhoto', body).addEventListener('click', async () => {
    await push(`/photo/${photo.day}`, null, 'DELETE')
    closeSheet()
    toast('Фото удалено')
  })
}

/* ----------------------------------------------------------------- фото */

function pickPhoto(day) {
  const input = document.createElement('input')
  input.type = 'file'
  input.accept = 'image/*'
  input.addEventListener('change', async () => {
    const file = input.files?.[0]
    if (!file) return
    toast('Загружаем фото…')
    try {
      const blob = await downscale(file)
      const form = new FormData()
      form.append('day', String(day))
      form.append('photo', blob, 'photo.jpg')
      await push('/photo', form)
      notify('success')
      toast('Фото сохранено ✨')
    } catch (e) {
      toast('Не получилось загрузить фото')
    }
  })
  input.click()
}

/* Снимок с телефона — это 3–8 МБ, которые не нужны ни для сравнения на
   экране, ни на диске Railway. Ужимаем до 1200 px по длинной стороне. */
function downscale(file, max = 1200) {
  return new Promise((resolve, reject) => {
    const image = new Image()
    image.onload = () => {
      const scale = Math.min(1, max / Math.max(image.width, image.height))
      const canvas = document.createElement('canvas')
      canvas.width = Math.round(image.width * scale)
      canvas.height = Math.round(image.height * scale)
      canvas.getContext('2d').drawImage(image, 0, 0, canvas.width, canvas.height)
      URL.revokeObjectURL(image.src)
      canvas.toBlob((blob) => (blob ? resolve(blob) : reject(new Error('encode_failed'))), 'image/jpeg', 0.86)
    }
    image.onerror = reject
    image.src = URL.createObjectURL(file)
  })
}

/* -------------------------------------------------------- экран «Узнать» */

function renderLearn() {
  const screen = el('screen-learn')
  const list = learnCategory === 'all'
    ? ARTICLES
    : ARTICLES.filter((article) => article.cat === learnCategory)

  screen.innerHTML = `
    <header class="head">
      <h1 class="head__title">Smile Academy</h1>
      <p class="head__sub">${ARTICLES.length} коротких материалов · прочитано ${state.reads.length}</p>
    </header>

    <button class="card" id="openQuiz" style="width:100%;text-align:left;display:flex;align-items:center;gap:13px">
      <span class="article-card__emoji" style="background:rgba(224,163,46,.14)">🔮</span>
      <span style="flex:1">
        <span class="article-card__title">Миф или правда</span>
        <span class="article-card__excerpt">Мини-игра на 5 вопросов${state.quiz.plays ? ` · лучший счёт ${state.quiz.best} из ${state.quiz.total}` : ''}</span>
      </span>
      <span class="row__chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg></span>
    </button>

    <div class="chips">
      ${CATEGORIES.map((category) => `
        <button class="chip ${category.id === learnCategory ? 'is-active' : ''}" data-cat="${category.id}">
          ${category.emoji} ${category.label}
        </button>`).join('')}
    </div>

    <div class="stack stagger">
      ${list.map((article) => {
        const read = state.reads.includes(article.id)
        return `
          <button class="article-card ${read ? 'is-read' : ''}" data-article="${article.id}">
            <span class="article-card__emoji">${article.emoji}</span>
            <span>
              <span class="article-card__title">${esc(article.title)}</span>
              <span class="article-card__excerpt">${esc(article.excerpt)}</span>
              <span class="article-card__meta">
                <span>${article.read} мин</span>
                ${read ? '<span class="badge-read">✓ прочитано</span>' : ''}
              </span>
            </span>
          </button>`
      }).join('')}
    </div>`

  screen.querySelectorAll('[data-cat]').forEach((chip) => {
    chip.addEventListener('click', () => {
      haptic()
      learnCategory = chip.dataset.cat
      renderLearn()
    })
  })
  screen.querySelectorAll('[data-article]').forEach((card) => {
    card.addEventListener('click', () => openArticle(card.dataset.article))
  })
  $('#openQuiz', screen).addEventListener('click', openQuiz)
}

function blockHtml(block) {
  if (block.t === 'h') return `<h3>${esc(block.v)}</h3>`
  if (block.t === 'list') return `<ul>${block.v.map((item) => `<li>${esc(item)}</li>`).join('')}</ul>`
  if (block.t === 'tip') return `<div class="reader__tip"><span>💡</span><span>${esc(block.v)}</span></div>`
  if (block.t === 'warn') return `<div class="reader__warn"><span>⚠️</span><span>${esc(block.v)}</span></div>`
  if (block.t === 'verdict') return `<div class="reader__verdict">${esc(block.v)}</div>`
  return `<p>${esc(block.v)}</p>`
}

function openArticle(id) {
  const article = ARTICLES.find((item) => item.id === id)
  if (!article) return
  haptic()
  openSheet(`
    <div class="reader">
      <span class="article-card__emoji" style="width:50px;height:50px;font-size:24px">${article.emoji}</span>
      <h2 style="margin-top:14px">${esc(article.title)}</h2>
      <p class="reader__meta">${article.read} мин чтения</p>
      ${ILLUSTRATIONS[article.id] ? `<figure class="figure">${ILLUSTRATIONS[article.id]}</figure>` : ''}
      <div class="reader__blocks">${article.blocks.map(blockHtml).join('')}</div>
      <button class="btn btn--ghost" data-close-sheet style="margin-top:26px">Понятно</button>
    </div>`)
  if (!state.reads.includes(id)) push('/read', { article_id: id }).catch(() => {})
}

/* ------------------------------------------------------------- мини-игра */

const QUIZ_ROUND = 5

function openQuiz() {
  haptic('medium')
  const questions = [...QUIZ].sort(() => Math.random() - 0.5).slice(0, QUIZ_ROUND)
  let index = 0
  let score = 0
  const results = []

  const body = openSheet('')

  function paintQuestion() {
    const question = questions[index]
    body.innerHTML = `
      <span class="quiz__count">ВОПРОС ${index + 1} ИЗ ${QUIZ_ROUND}</span>
      <p class="quiz__claim">${esc(question.claim)}</p>
      <div class="quiz__actions">
        <button class="btn btn--ghost" data-answer="true">Правда</button>
        <button class="btn btn--ghost" data-answer="false">Миф</button>
      </div>
      <div class="quiz__dots">
        ${Array.from({ length: QUIZ_ROUND }, (_, i) => `<i class="${results[i] === true ? 'ok' : results[i] === false ? 'no' : ''}"></i>`).join('')}
      </div>`
    body.querySelectorAll('[data-answer]').forEach((button) => {
      button.addEventListener('click', () => paintAnswer(button.dataset.answer === 'true'))
    })
  }

  function paintAnswer(answer) {
    const question = questions[index]
    const correct = answer === question.truth
    results[index] = correct
    if (correct) score += 1
    notify(correct ? 'success' : 'warning')
    body.innerHTML = `
      <span class="quiz__count">ВОПРОС ${index + 1} ИЗ ${QUIZ_ROUND}</span>
      <p class="quiz__claim" style="margin-bottom:14px">${esc(question.claim)}</p>
      <div class="quiz__verdict ${question.truth ? 'quiz__verdict--true' : 'quiz__verdict--myth'}">
        ${question.verdict}
      </div>
      <p class="quiz__explain">${esc(question.explain)}</p>
      <p class="quiz__result ${correct ? 'is-ok' : 'is-no'}">
        ${correct ? '✓ Ты ответил(а) верно' : '✗ В этот раз мимо — теперь знаешь'}
      </p>
      <div class="quiz__dots">
        ${Array.from({ length: QUIZ_ROUND }, (_, i) => `<i class="${results[i] === true ? 'ok' : results[i] === false ? 'no' : ''}"></i>`).join('')}
      </div>
      <button class="btn" id="quizNext" style="margin-top:20px">
        ${index === QUIZ_ROUND - 1 ? 'Посмотреть результат' : 'Следующий вопрос'}
      </button>`
    $('#quizNext', body).addEventListener('click', () => {
      haptic()
      if (index === QUIZ_ROUND - 1) paintResult()
      else { index += 1; paintQuestion() }
    })
  }

  async function paintResult() {
    const expert = score >= 4
    body.innerHTML = `
      <div class="finale">
        <div class="finale__emoji">${expert ? '🦷' : '📚'}</div>
        <h2 class="finale__title">${score} из ${QUIZ_ROUND}</h2>
        <p class="finale__lead">${expert
          ? 'Теперь ты Smile Expert. Мифы про отбеливание тебя больше не обманут.'
          : 'Неплохо! Загляни в Smile Academy — там всё это разобрано по полочкам.'}</p>
        <button class="btn" data-close-sheet style="margin-top:22px">Готово</button>
      </div>`
    if (expert) confetti()
    await push('/quiz', { score, total: QUIZ_ROUND }).catch(() => {})
  }

  paintQuestion()
}

/* ----------------------------------------------------- экран «Спросить» */

const CHAT_SUGGESTS = [
  'Можно ли пить кофе сразу после процедуры?',
  'Почему появилась чувствительность?',
  'Сколько держится результат?',
  'Можно ли пользоваться при брекетах?',
]

function renderAsk() {
  const screen = el('screen-ask')
  screen.innerHTML = `
    <header class="head">
      <h1 class="head__title">Спросить</h1>
      <p class="head__sub">ИИ-консультант отвечает на вопросы про зубы, улыбку и SmileKit</p>
    </header>
    <div class="chat" id="chat"></div>
    <div>
      <form class="composer" id="composer">
        <textarea id="chatInput" rows="1" placeholder="Спроси про зубы или курс…" maxlength="500"></textarea>
        <button class="composer__send" type="submit" id="chatSend" aria-label="Отправить">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="M4.5 12h15M13 5.5 19.5 12 13 18.5"/></svg>
        </button>
      </form>
    </div>`

  paintChat()

  const input = $('#chatInput', screen)
  input.addEventListener('input', () => {
    input.style.height = 'auto'
    input.style.height = `${Math.min(120, input.scrollHeight)}px`
  })
  $('#composer', screen).addEventListener('submit', (event) => {
    event.preventDefault()
    const text = input.value.trim()
    if (!text || chatBusy) return
    input.value = ''
    input.style.height = 'auto'
    sendQuestion(text)
  })
}

function paintChat() {
  const chat = el('chat')
  if (!chat) return
  // Подсказки нужны только на пустом чате: после первого вопроса они уже
  // мешают — оттесняют переписку вниз и повторяют то, что клиент спросил.
  const intro = `
    <div class="bubble bubble--bot">Привет 💜 Я консультант SmileKit. Отвечаю только на вопросы про зубы, улыбку, уход и наш набор — всё остальное лучше спросить не у меня.</div>
    ${chatMessages.length ? '' : `
      <div class="suggests">
        ${CHAT_SUGGESTS.map((text) => `<button class="suggest" data-suggest="${esc(text)}">${esc(text)}</button>`).join('')}
      </div>`}`
  chat.innerHTML = intro + chatMessages.map((message) => `
    <div class="bubble bubble--${message.role === 'user' ? 'me' : 'bot'}">${esc(message.content)}</div>
  `).join('') + (chatBusy ? `
    <div class="bubble bubble--bot"><span class="typing"><i></i><i></i><i></i></span></div>` : '')

  chat.querySelectorAll('[data-suggest]').forEach((button) => {
    button.addEventListener('click', () => {
      if (!chatBusy) sendQuestion(button.dataset.suggest)
    })
  })
  requestAnimationFrame(() => window.scrollTo({ top: document.body.scrollHeight, behavior: 'smooth' }))
}

async function sendQuestion(text) {
  haptic()
  chatMessages.push({ role: 'user', content: text })
  chatBusy = true
  paintChat()
  try {
    const answer = await api('/ask', { method: 'POST', body: JSON.stringify({ text }) })
    chatMessages.push({ role: 'assistant', content: answer.answer })
  } catch (e) {
    chatMessages.push({
      role: 'assistant',
      content: 'Не получилось ответить прямо сейчас. Попробуй ещё раз через минуту или напиши нам в чат — менеджер поможет.',
    })
  } finally {
    chatBusy = false
    paintChat()
  }
}

async function loadChat() {
  try {
    const data = await api('/ask')
    chatMessages = data.messages || []
  } catch (e) {
    chatMessages = []
  }
  paintChat()
}

/* ------------------------------------------------------ экран «Профиль» */

const GOAL_LABELS = {
  refresh: 'Немного освежить улыбку',
  lighter: 'Сделать зубы заметно светлее',
  max: 'Максимальный результат',
}
const DRINK_LABELS = { coffee: 'Кофе', tea: 'Чай', soda: 'Газированные напитки', none: 'Почти ничего красящего' }
const SENSITIVITY_LABELS = { yes: 'Есть', sometimes: 'Иногда', no: 'Нет' }
const REMINDER_HOURS = [9, 12, 18, 20, 21, 22]

function renderProfile() {
  const screen = el('screen-profile')
  const { reward } = state
  const initial = (state.user.first_name || 'S').trim().charAt(0).toUpperCase()

  screen.innerHTML = `
    <header class="head">
      <div class="profile__head">
        <span class="avatar">${esc(initial)}</span>
        <span>
          <h1 class="head__title" style="font-size:21px">${esc(state.user.first_name || 'Профиль')}</h1>
          <p class="head__sub">День ${state.course.day} из ${state.course.total_days} · Smile Score ${state.score}</p>
        </span>
      </div>
    </header>

    <div class="stack stagger">
      ${reward.code ? `
        <div class="reward">
          <span class="eyebrow" style="color:rgba(255,255,255,.8)">Моя награда</span>
          <div class="reward__pct">−${reward.percent}%</div>
          <div class="reward__label">На следующий набор SmileKit</div>
          <div class="reward__code">${esc(reward.code)}</div>
          <div class="reward__note">Действует до ${new Date(reward.expires_at * 1000).toLocaleDateString('ru-RU')}. Назови код менеджеру в чате с ботом.</div>
        </div>` : `
        <div class="reward reward--locked">
          <span class="eyebrow">Награда за курс</span>
          <div class="reward__pct">−30%</div>
          <div class="reward__label">${reward.unlocked
            ? 'Открыто! Забери скидку на следующий набор.'
            : `Откроется после ${reward.procedures_required} процедур. Осталось ${reward.left}.`}</div>
          <div class="reward__meter"><i style="width:${Math.round((reward.procedures_done / reward.procedures_required) * 100)}%"></i></div>
          ${reward.unlocked ? '<button class="btn" id="claimReward2" style="margin-top:16px">Забрать скидку −30%</button>' : ''}
        </div>`}

      <div class="card">
        <span class="eyebrow">Мой SmileKit</span>
        <div class="tiles" style="margin-top:12px">
          <div class="tile">
            <div class="tile__value">${state.stats.procedures_done}/${state.stats.procedures_total}</div>
            <div class="tile__label">Процедур</div>
          </div>
          <div class="tile">
            <div class="tile__value">${state.streak}</div>
            <div class="tile__label">Серия</div>
          </div>
          <div class="tile">
            <div class="tile__value">${state.stats.articles_read}</div>
            <div class="tile__label">Прочитано</div>
          </div>
        </div>
      </div>

      <div class="card">
        <span class="eyebrow">Мои ответы</span>
        <div style="margin-top:6px">
          <div class="row">
            <span><span class="row__title">Цель</span><span class="row__sub">${GOAL_LABELS[state.user.goal] || '—'}</span></span>
          </div>
          <div class="row">
            <span><span class="row__title">Чаще пью</span><span class="row__sub">${DRINK_LABELS[state.user.drink] || '—'}</span></span>
          </div>
          <div class="row">
            <span><span class="row__title">Чувствительность</span><span class="row__sub">${SENSITIVITY_LABELS[state.user.sensitivity] || '—'}</span></span>
          </div>
        </div>
      </div>

      <div class="card">
        <span class="eyebrow">Напоминания</span>
        <div class="row" style="margin-top:6px">
          <span>
            <span class="row__title">Напоминать о курсе</span>
            <span class="row__sub">Бот напишет в чат, если день не отмечен</span>
          </span>
          <button class="switch ${state.user.reminders_on ? 'is-on' : ''}" id="toggleReminders"><i></i></button>
        </div>
        ${state.user.reminders_on ? `
          <div class="hours">
            ${REMINDER_HOURS.map((hour) => `
              <button class="hour ${state.user.reminder_hour === hour ? 'is-active' : ''}" data-hour="${hour}">${hour}:00</button>
            `).join('')}
          </div>
          <p class="muted" style="margin-top:10px">Время по Бишкеку.</p>` : ''}
      </div>

      ${homeScreenStatus === 'missed' || homeScreenStatus === 'unknown' ? `
        <button class="card install" id="addHome">
          <span class="install__icon">🦷</span>
          <span>
            <span class="row__title">Вынести на экран телефона</span>
            <span class="row__sub">Иконка рядом с другими приложениями — открывается в одно касание</span>
          </span>
        </button>` : ''}

      <div class="card">
        <span class="eyebrow">Помощь</span>
        <div style="margin-top:6px">
          <button class="row" id="openSupport">
            <span><span class="row__title">У меня вопрос</span><span class="row__sub">Напишем менеджеру с контекстом курса</span></span>
            <span class="row__chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg></span>
          </button>
          <button class="row" id="restartCourse">
            <span><span class="row__title">Начать курс заново</span><span class="row__sub">Открыл новую коробку — обнулим отметки и фото</span></span>
            <span class="row__chevron"><svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 5 7 7-7 7"/></svg></span>
          </button>
        </div>
      </div>

      <p class="muted center" style="padding:6px 20px 0">Smile Score — игровой показатель соблюдения программы, а не медицинская оценка.</p>
    </div>`

  $('#claimReward2', screen)?.addEventListener('click', claimReward)
  $('#addHome', screen)?.addEventListener('click', addToHomeScreen)
  $('#toggleReminders', screen).addEventListener('click', async () => {
    haptic()
    await push('/settings', { reminders_on: !state.user.reminders_on })
  })
  screen.querySelectorAll('[data-hour]').forEach((button) => {
    button.addEventListener('click', async () => {
      haptic()
      await push('/settings', { reminder_hour: Number(button.dataset.hour) })
      toast(`Будем напоминать в ${button.dataset.hour}:00`)
    })
  })
  $('#openSupport', screen).addEventListener('click', openSupport)
  $('#restartCourse', screen).addEventListener('click', confirmRestart)
}

async function claimReward() {
  haptic('medium')
  try {
    await push('/reward', {})
    confetti()
    notify('success')
    toast('Промокод отправлен в чат с ботом 🎁')
  } catch (e) {
    toast('Награда откроется после 6 процедур')
  }
}

const SUPPORT_TOPICS = [
  { id: 'sensitivity', emoji: '🦷', label: 'Появилась чувствительность' },
  { id: 'no_result', emoji: '😕', label: 'Не вижу результат' },
  { id: 'package', emoji: '📦', label: 'Проблема с набором' },
  { id: 'how_to', emoji: '❓', label: 'Не знаю, как использовать' },
  { id: 'other', emoji: '💬', label: 'Другой вопрос' },
]

function openSupport() {
  haptic()
  const body = openSheet(`
    <span class="eyebrow">Поддержка</span>
    <h2 style="margin-top:10px;font-size:22px;color:var(--purple-deep)">Что случилось?</h2>
    <div class="onb__options" style="margin-top:16px">
      ${SUPPORT_TOPICS.map((topic) => `
        <button class="option" data-topic="${topic.id}">
          <span style="font-size:20px">${topic.emoji}</span>
          <span class="option__text">${topic.label}</span>
        </button>`).join('')}
    </div>`)

  body.querySelectorAll('[data-topic]').forEach((button) => {
    button.addEventListener('click', () => paintSupportForm(button.dataset.topic))
  })
}

function paintSupportForm(topic) {
  haptic()
  const label = SUPPORT_TOPICS.find((item) => item.id === topic).label
  const body = el('sheetBody')
  body.innerHTML = `
    <span class="eyebrow">Поддержка</span>
    <h2 style="margin-top:10px;font-size:22px;color:var(--purple-deep)">${label}</h2>
    <p class="muted" style="margin-top:10px">Менеджер увидит, на каком дне курса ты сейчас и сколько процедур сделано — повторять это не нужно.</p>
    <form class="composer" id="supportForm" style="position:static;margin-top:16px;max-width:none">
      <textarea id="supportText" rows="3" placeholder="Расскажи подробнее (необязательно)" maxlength="1000"></textarea>
    </form>
    <button class="btn" id="supportSend" style="margin-top:14px">Написать нам</button>`

  $('#supportSend', body).addEventListener('click', async () => {
    haptic('medium')
    try {
      await api('/support', {
        method: 'POST',
        body: JSON.stringify({ topic, text: $('#supportText', body).value.trim() }),
      })
      notify('success')
      closeSheet()
      toast('Отправили менеджеру — ответим в чате 💜')
    } catch (e) {
      toast('Не получилось отправить. Попробуй ещё раз.')
    }
  })
}

function confirmRestart() {
  haptic()
  const body = openSheet(`
    <span class="eyebrow">Новый курс</span>
    <h2 style="margin-top:10px;font-size:22px;color:var(--purple-deep)">Начать заново?</h2>
    <p class="muted" style="margin-top:10px">Отметки дней и фото прогресса будут удалены, счётчик пойдёт с первого дня. Прочитанные материалы и ответы онбординга останутся.</p>
    <button class="btn" id="restartYes" style="margin-top:20px">Да, начать заново</button>
    <button class="btn btn--ghost" data-close-sheet style="margin-top:10px">Отмена</button>`)
  $('#restartYes', body).addEventListener('click', async () => {
    await push('/restart', {})
    closeSheet()
    go('today')
    toast('Курс запущен заново 💜')
  })
}

/* --------------------------------------------------------- финал курса */

function renderFinale() {
  const first = state.photos.find((p) => p.day === 1)
  const last = [...state.photos].reverse().find((p) => p.day !== 1)
  const body = openSheet(`
    <div class="finale">
      <div class="finale__emoji">🎉</div>
      <h2 class="finale__title">Ты завершил(а) SmileKit</h2>
      <p class="finale__lead">Посмотри, как изменилась твоя улыбка за ${state.course.total_days} дней.</p>
    </div>
    ${first && last ? `
      <div class="compare" id="compare" style="--split:50%;margin-top:20px;border-radius:22px">
        <img src="${first.url}" alt="До">
        <img class="compare__after" src="${last.url}" alt="После">
        <span class="compare__tag compare__tag--left">До</span>
        <span class="compare__tag compare__tag--right">После</span>
        <span class="compare__handle"><span class="compare__grip">
          <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"><path d="m9 6-5 6 5 6M15 6l5 6-5 6"/></svg>
        </span></span>
      </div>` : ''}
    <div class="tiles" style="margin-top:18px">
      <div class="tile"><div class="tile__value">${state.stats.procedures_done}/${state.stats.procedures_total}</div><div class="tile__label">Процедур</div></div>
      <div class="tile"><div class="tile__value">${state.streak}</div><div class="tile__label">Серия</div></div>
      <div class="tile"><div class="tile__value">${state.stats.articles_read}</div><div class="tile__label">Материалов</div></div>
    </div>
    ${state.reward.unlocked && !state.reward.code
      ? '<button class="btn" id="finaleReward" style="margin-top:20px">Забрать скидку −30%</button>'
      : '<button class="btn btn--ghost" data-close-sheet style="margin-top:20px">Закрыть</button>'}`)
  setupCompare(body)
  confetti()
  $('#finaleReward', body)?.addEventListener('click', async () => {
    await claimReward()
    closeSheet()
  })
  localStorage.setItem('smilekit_finale_seen', '1')
}

/* ------------------------------------------------------------ навигация */

const RENDERERS = {
  today: renderToday,
  progress: renderProgress,
  learn: renderLearn,
  ask: renderAsk,
  profile: renderProfile,
}

function movePill() {
  const bar = el('tabbar')
  const active = $('.tab.is-active', bar)
  const pill = el('tabPill')
  if (!active || !pill) return
  pill.style.width = `${active.offsetWidth}px`
  pill.style.transform = `translateX(${active.offsetLeft}px)`
}

function go(tab, direction = 0) {
  activeTab = tab
  document.querySelectorAll('.screen').forEach((screen) => {
    screen.hidden = screen.dataset.tab !== tab
  })
  document.querySelectorAll('.tab').forEach((button) => {
    button.classList.toggle('is-active', button.dataset.goto === tab)
  })
  movePill()
  RENDERERS[tab]()
  if (tab === 'ask') loadChat()
  window.scrollTo({ top: 0 })
  syncBackButton()
  // При переходе свайпом экран приезжает с той стороны, куда вёл палец —
  // без этого жест ощущается как перезагрузка страницы, а не как листание.
  const screen = el(`screen-${tab}`)
  if (direction && screen) {
    screen.style.animation = 'none'
    void screen.offsetWidth
    screen.style.animation = `slide-${direction > 0 ? 'left' : 'right'} .34s var(--ease) both`
  }
}

/* Горизонтальный свайп между вкладками. Вертикальные жесты не трогаем —
   это скролл, а внутри карточки сравнения «до/после» жест уже занят
   шторкой, поэтому оттуда листание не запускаем. */
function setupSwipe() {
  const NOPE = '.compare, .chips, textarea, input, .track'
  let startX = 0
  let startY = 0
  let tracking = false

  document.addEventListener('touchstart', (event) => {
    if (!el('sheet').hidden || event.touches.length !== 1) return
    if (event.target.closest(NOPE)) return
    startX = event.touches[0].clientX
    startY = event.touches[0].clientY
    tracking = true
  }, { passive: true })

  document.addEventListener('touchend', (event) => {
    if (!tracking) return
    tracking = false
    const dx = event.changedTouches[0].clientX - startX
    const dy = event.changedTouches[0].clientY - startY
    if (Math.abs(dx) < 64 || Math.abs(dx) < Math.abs(dy) * 1.6) return
    const index = TABS.indexOf(activeTab)
    const next = TABS[index + (dx < 0 ? 1 : -1)]
    if (!next) return
    haptic()
    go(next, dx < 0 ? 1 : -1)
  }, { passive: true })
}

/* Bot API 8.0: иконка Mini App прямо на домашнем экране телефона —
   запускается без открытия Telegram и без адресной строки. Метод есть не во
   всех клиентах, поэтому строку в профиле показываем только там, где он
   поддерживается и ярлык ещё не поставлен. */
let homeScreenStatus = 'unsupported'

function detectHomeScreen() {
  if (!tg?.isVersionAtLeast?.('8.0') || !tg.checkHomeScreenStatus) return
  try {
    tg.checkHomeScreenStatus((status) => {
      homeScreenStatus = status
      if (activeTab === 'profile') renderProfile()
    })
  } catch (e) {}
}

function addToHomeScreen() {
  haptic('medium')
  try {
    tg.addToHomeScreen()
    // Клиент показывает своё системное окно; статус перечитываем, когда
    // пользователь в него ответит.
    tg.onEvent?.('homeScreenAdded', () => {
      homeScreenStatus = 'added'
      notify('success')
      toast('Готово — иконка на домашнем экране 💜')
      if (activeTab === 'profile') renderProfile()
    })
  } catch (e) {
    toast('Твой Telegram пока не умеет добавлять иконку')
  }
}

function render() {
  if (!state.user.onboarded) {
    renderOnboarding()
    return
  }
  if (!el('screen-today')) restoreShell()
  el('tabbar').hidden = false
  el('app').classList.remove('app--plain')
  RENDERERS[activeTab]()
  movePill()
}

/* ----------------------------------------------------------------- старт */

async function boot() {
  try {
    tg?.ready()
    tg?.expand()
    tg?.setHeaderColor?.('#F4F0FE')
    tg?.setBackgroundColor?.('#F4F0FE')
    tg?.setBottomBarColor?.('#F4F0FE')
    // Свайп вниз в Telegram закрывает приложение — здесь он мешал бы скроллу
    // и жесту шторки «до/после».
    tg?.disableVerticalSwipes?.()
    // Bot API 8.0: во весь экран, без шапки Telegram — ради этого и считаются
    // safe area в CSS, иначе шапка приложения уезжает под вырез.
    if (tg?.isVersionAtLeast?.('8.0')) {
      tg.requestFullscreen?.()
      tg.onEvent?.('fullscreenChanged', movePill)
      tg.onEvent?.('safeAreaChanged', movePill)
      tg.onEvent?.('contentSafeAreaChanged', movePill)
    }
    tg?.BackButton?.onClick?.(handleBack)
    tg?.onEvent?.('backButtonClicked', handleBack)
  } catch (e) {}
  detectHomeScreen()
  resetFactOfTheDay()

  try {
    state = await api('/state')
  } catch (e) {
    // Причину видно и клиенту, и в логах сервера: «не открывается» одинаково
    // выглядит и когда страницу открыли вне Telegram, и когда подпись запуска
    // не сошлась, а чинится это по-разному.
    const REASONS = {
      empty: 'Страница открыта вне Telegram — данные запуска не переданы.',
      no_hash: 'Telegram не передал подпись запуска.',
      bad_signature: 'Подпись запуска не сошлась с ботом.',
      stale: 'Сессия запуска устарела. Закрой и открой приложение заново.',
      bad_user: 'Telegram передал данные профиля в неожиданном виде.',
      no_user_id: 'В данных запуска нет профиля Telegram.',
    }
    let detail = REASONS[e.reason] || (e.status ? `Сервер ответил ${e.status}.` : 'Сервер недоступен.')
    if (e.reason === 'empty') {
      detail += tg
        ? ' Telegram не передал данные запуска этой странице.'
        : ' Скрипт Telegram не загрузился — проверь соединение и открой снова.'
    }
    el('boot').innerHTML = `
      <div class="card" style="max-width:330px;text-align:center">
        <p style="font-weight:800;color:var(--purple-deep)">Не удалось открыть программу</p>
        <p class="muted" style="margin-top:8px">${detail}</p>
        <p class="muted" style="margin-top:10px">Открой приложение кнопкой «Программа» в чате с ботом или командой /app.</p>
        <p style="margin-top:12px;font-size:11px;color:var(--ink-soft)">код: ${e.reason || e.status || 'network'}</p>
      </div>`
    return
  }

  document.querySelectorAll('.tab').forEach((button) => {
    button.addEventListener('click', () => { haptic(); go(button.dataset.goto) })
  })
  window.addEventListener('resize', movePill)
  setupSwipe()

  el('app').hidden = false
  render()
  el('boot').classList.add('is-gone')
  setTimeout(() => { el('boot').hidden = true }, 460)

  // Финальный экран показывается один раз — на дне 14, когда он отмечен.
  if (state.course.day >= state.course.total_days
      && state.check_ins[String(state.course.total_days)]
      && !localStorage.getItem('smilekit_finale_seen')) {
    setTimeout(renderFinale, 700)
  }
}

boot()
