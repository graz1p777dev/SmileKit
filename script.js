// Smilekit — интерактив лендинга

const calm = matchMedia('(prefers-reduced-motion: reduce)').matches

// Переход по прямой ссылке вида /#faq: браузер прыгает к якорю один раз,
// сразу на старте, пока страница ещё в системном шрифте. Когда Google Fonts
// (display=swap) подменяют начертание, высота текста над якорем меняется,
// и секция уезжает — снаружи это выглядит как "ссылка ведёт не туда".
// Пересчитываем позицию после подгрузки шрифтов и после полной отрисовки.
if (location.hash) {
  const settleHash = () => {
    const target = document.querySelector(location.hash)
    if (!target) return
    // behavior:'instant' — не 'smooth': плавная прокрутка идёт через
    // requestAnimationFrame, а его браузеры замораживают в фоновой вкладке
    // (например, если ссылку из бота открыли не активной). Анимация тогда
    // застревает на первом кадре, и страница остаётся у самого верха.
    target.scrollIntoView({ block: 'start', behavior: 'instant' })
    // Секция, на которую прыгнули, не должна ждать своей очереди в
    // IntersectionObserver — иначе гость по прямой ссылке первые доли
    // секунды видит пустоту вместо контента (та же причина, по которой
    // hero проигрывается сразу, без ожидания скролла).
    target.querySelectorAll('.reveal').forEach((el) => el.classList.add('is-visible'))
    target.classList.add('is-visible')
  }
  // instant-скролл синхронный — в отличие от smooth, повторные вызовы
  // не анимируются и не накапливают смещение, поэтому дёргаем несколько раз
  // на случай, если картинки/шрифты досчитают высоту страницы позже.
  settleHash()
  document.fonts?.ready.then(settleHash)
  addEventListener('load', settleHash)
  setTimeout(settleHash, 400)
  setTimeout(settleHash, 1200)
}

// Заголовки разбиваем на слова, чтобы они выезжали из-под маски
document.querySelectorAll('.js-split').forEach((title) => {
  const parts = []

  title.childNodes.forEach((node) => {
    if (node.nodeType !== Node.TEXT_NODE) {
      parts.push(node.cloneNode(true))
      return
    }
    // неразрывный пробел не разделяет — «всего&nbsp;за» остаётся одним словом
    node.textContent.split(/([^\S\u00a0]+)/).forEach((chunk) => {
      if (!chunk) return
      if (!chunk.trim()) return parts.push(document.createTextNode(chunk))
      const outer = document.createElement('span')
      const inner = document.createElement('span')
      outer.className = 'word'
      inner.textContent = chunk
      outer.append(inner)
      parts.push(outer)
    })
  })

  title.replaceChildren(...parts)
  title.querySelectorAll('.word > span').forEach((word, i) => {
    word.style.setProperty('--d', i * 0.06 + 's')
  })
})

// Появление блоков при прокрутке, соседние — с нарастающей задержкой
document.querySelectorAll('.cards, .steps, .hero__copy').forEach((group) => {
  group.querySelectorAll(':scope > .reveal').forEach((el, i) => {
    el.style.setProperty('--d', i * 0.09 + 's')
  })
})

const observer = new IntersectionObserver(
  (entries) => {
    entries.forEach((entry) => {
      if (entry.isIntersecting) {
        entry.target.classList.add('is-visible')
        observer.unobserve(entry.target)
      }
    })
  },
  { threshold: 0.15, rootMargin: '0px 0px -40px' },
)

document.querySelectorAll('.reveal').forEach((el) => observer.observe(el))

// Первый экран проигрывается сразу после загрузки, а не по появлению в вьюпорте
setTimeout(() => {
  document.querySelectorAll('.hero .reveal').forEach((el) => {
    observer.unobserve(el)
    el.classList.add('is-visible')
  })
}, 80)

// Шапка и индикатор прокрутки
const header = document.querySelector('.header')
const progress = document.querySelector('.progress')

const onScroll = () => {
  header.classList.toggle('is-stuck', scrollY > 12)
  const max = document.documentElement.scrollHeight - innerHeight
  progress.style.transform = `scaleX(${max > 0 ? scrollY / max : 0})`
}
addEventListener('scroll', onScroll, { passive: true })
onScroll()

// Счётчики в hero
const countUp = (el) => {
  // исходная строка — единственный источник правды, кадры её не перезаписывают
  const final = el.dataset.count || el.textContent
  el.dataset.count = final

  const match = final.match(/([\d,]+)/)
  if (!match) return
  if (document.hidden) return (el.textContent = final)

  const raw = match[1]
  const decimals = raw.includes(',') ? 1 : 0
  const target = parseFloat(raw.replace(',', '.'))
  const start = performance.now()

  const tick = (now) => {
    const p = Math.min((now - start) / 1100, 1)
    if (p === 1) return (el.textContent = final)
    const value = (target * (1 - Math.pow(1 - p, 3))).toFixed(decimals).replace('.', ',')
    el.textContent = final.replace(/([\d,]+)/, value)
    requestAnimationFrame(tick)
  }

  requestAnimationFrame(tick)
}

if (!calm) {
  const counters = new IntersectionObserver((entries) => {
    entries.forEach((entry) => {
      if (!entry.isIntersecting) return
      countUp(entry.target)
      counters.unobserve(entry.target)
    })
  })
  document.querySelectorAll('[data-count]').forEach((el) => counters.observe(el))
}

// Упаковка слегка поворачивается за курсором
const visual = document.querySelector('.hero__visual')
const pack = document.querySelector('.pack')

if (!calm && matchMedia('(hover: hover)').matches) {
  visual.addEventListener('pointermove', (e) => {
    const box = visual.getBoundingClientRect()
    const x = (e.clientX - box.left) / box.width - 0.5
    const y = (e.clientY - box.top) / box.height - 0.5
    pack.style.setProperty('--ry', x * 12 + 'deg')
    pack.style.setProperty('--rx', -y * 10 + 'deg')
  })
  visual.addEventListener('pointerleave', () => {
    pack.style.setProperty('--ry', '0deg')
    pack.style.setProperty('--rx', '0deg')
  })
}

// Подсветка карточек за курсором
document.querySelectorAll('.review').forEach((card) => {
  card.addEventListener('pointermove', (e) => {
    const box = card.getBoundingClientRect()
    card.style.setProperty('--mx', e.clientX - box.left + 'px')
    card.style.setProperty('--my', e.clientY - box.top + 'px')
  })
})

// Сравнение «до / после»
document.querySelectorAll('.compare').forEach((compare) => {
  const range = compare.querySelector('.compare__range')
  range.addEventListener('input', () => {
    compare.dataset.touched = 'true'
    compare.style.setProperty('--pos', range.value + '%')
  })
})

// Первая карточка один раз показывает, что ползунок можно тянуть
const firstCompare = document.querySelector('.compare')

if (!calm && firstCompare) {
  const hint = new IntersectionObserver((entries) => {
    if (!entries[0].isIntersecting) return
    hint.disconnect()

    const range = firstCompare.querySelector('.compare__range')
    const start = performance.now()

    const play = (now) => {
      if (firstCompare.dataset.touched) return
      const p = Math.min((now - start) / 2600, 1)
      const pos = 50 + Math.sin(p * Math.PI * 2) * 32
      firstCompare.style.setProperty('--pos', pos + '%')
      range.value = pos
      if (p < 1) requestAnimationFrame(play)
    }

    setTimeout(() => requestAnimationFrame(play), 400)
  })

  hint.observe(firstCompare)
}

// Слайдер результатов
document.querySelectorAll('[data-slide]').forEach((btn) => {
  btn.addEventListener('click', () => {
    const track = document.getElementById(btn.dataset.target)
    const gap = parseFloat(getComputedStyle(track).columnGap) || 0
    const step = track.firstElementChild.offsetWidth + gap
    track.scrollBy({ left: btn.dataset.slide === 'next' ? step : -step, behavior: 'smooth' })
  })
})

// Лента скриншотов: дублируем набор, чтобы прокрутка была бесшовной
const shots = document.getElementById('shots')

if (shots) {
  shots.append(
    ...[...shots.children].map((node) => {
      const copy = node.cloneNode(true)
      copy.setAttribute('aria-hidden', 'true')
      return copy
    }),
  )
}

// FAQ — раскрытие через grid-template-rows (в CSS), тут только переключаем состояние
document.querySelectorAll('.faq__q').forEach((question) => {
  question.addEventListener('click', () => {
    const isOpen = question.getAttribute('aria-expanded') === 'true'

    document.querySelectorAll('.faq__q[aria-expanded="true"]').forEach((other) => {
      other.setAttribute('aria-expanded', 'false')
    })

    if (!isOpen) question.setAttribute('aria-expanded', 'true')
  })
})
